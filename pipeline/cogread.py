"""
cogread.py — A small, dependency-free reader for Cloud-Optimized GeoTIFFs.

Why this exists: two of Altis's free data sources ship as GeoTIFFs — CHIRPS
daily rainfall (LZW-compressed COGs on data.chc.ucsb.edu) and the Sen1Floods11
hand-labelled flood chips (Phase F training data). The usual readers
(rasterio/GDAL, or tifffile + imagecodecs) bring native dependencies the
Railway image doesn't have, and we only ever need a small window of a big
file. A COG is tiled precisely so a client can fetch just the tiles it needs
with HTTP Range requests, so this module does exactly that:

    reader = CogReader(fetch)      # fetch(offset, length) -> bytes
    arr, transform = reader.read_window(lon_min, lat_min, lon_max, lat_max)

Supported (everything our sources use, nothing more): classic little/big
endian TIFF and BigTIFF, tiled or stripped, single-band or band-separate
(planar) multi-band, compression none / LZW / DEFLATE,
predictor 1 / 2 / 3, single-band uint8 / int8 / uint16 / int16 / uint32 /
int32 / float32 / float64, GeoTIFF pixel-scale + tiepoint georeferencing
(read_window assumes a lon/lat CRS; projected grids use read_rows_cols). Anything else raises ValueError rather than returning garbage.
"""
from __future__ import annotations

import struct
import zlib

import numpy as np

_TYPE_SIZES = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8,
               11: 4, 12: 8, 16: 8, 17: 8, 18: 8}
_TYPE_FMT = {1: 'B', 2: 's', 3: 'H', 4: 'I', 5: 'II', 6: 'b', 7: 'B', 8: 'h',
             9: 'i', 10: 'ii', 11: 'f', 12: 'd', 16: 'Q', 17: 'q', 18: 'Q'}

TAG_WIDTH, TAG_HEIGHT = 256, 257
TAG_BITS, TAG_COMPRESSION = 258, 259
TAG_STRIP_OFFSETS, TAG_SPP, TAG_ROWS_PER_STRIP, TAG_STRIP_BYTES = 273, 277, 278, 279
TAG_PREDICTOR = 317
TAG_PLANAR = 284
TAG_TILE_W, TAG_TILE_H, TAG_TILE_OFFSETS, TAG_TILE_BYTES = 322, 323, 324, 325
TAG_SAMPLE_FORMAT = 339
TAG_PIXEL_SCALE, TAG_TIEPOINT = 33550, 33922
TAG_NODATA = 42113


def lzw_decode(data: bytes) -> bytes:
    """TIFF-flavour LZW (MSB-first codes, 'early change' width bump)."""
    out = bytearray()
    table = [bytes([i]) for i in range(256)] + [b'', b'']
    width = 9
    bitbuf = 0
    bitcount = 0
    prev = None
    for byte in data:
        bitbuf = (bitbuf << 8) | byte
        bitcount += 8
        while bitcount >= width:
            bitcount -= width
            code = (bitbuf >> bitcount) & ((1 << width) - 1)
            if code == 256:                       # clear
                table = table[:258]
                width = 9
                prev = None
                continue
            if code == 257:                       # end of information
                return bytes(out)
            if prev is None:
                entry = table[code]
                out += entry
                prev = entry
                continue
            if code < len(table):
                entry = table[code]
                table.append(prev + entry[:1])
            elif code == len(table):
                entry = prev + prev[:1]
                table.append(entry)
            else:
                raise ValueError('corrupt LZW stream')
            out += entry
            prev = entry
            # Early change: widen one code before the table actually fills.
            if len(table) + 1 >= (1 << width) and width < 12:
                width += 1
        bitbuf &= (1 << bitcount) - 1
    return bytes(out)


def _dtype(bits: int, fmt: int, big_endian: bool) -> np.dtype:
    kind = {1: 'u', 2: 'i', 3: 'f'}.get(fmt)
    if kind is None or bits not in (8, 16, 32, 64) or (kind == 'f' and bits < 32):
        raise ValueError(f'unsupported sample format {fmt}/{bits}')
    return np.dtype(f"{'>' if big_endian else '<'}{kind}{bits // 8}")


class CogReader:
    """Reads the first (full-resolution) image of a single-band GeoTIFF."""

    def __init__(self, fetch, header_bytes: int = 65536):
        self._fetch = fetch
        head = fetch(0, header_bytes)
        self._head = head
        order = head[:2]
        if order not in (b'II', b'MM'):
            raise ValueError('not a TIFF')
        self.big_endian = order == b'MM'
        e = '>' if self.big_endian else '<'
        self._e = e
        magic = struct.unpack(e + 'H', head[2:4])[0]
        if magic == 42:
            self.bigtiff = False
            ifd = struct.unpack(e + 'I', head[4:8])[0]
        elif magic == 43:
            self.bigtiff = True
            ifd = struct.unpack(e + 'Q', head[8:16])[0]
        else:
            raise ValueError('bad TIFF magic')
        self.tags = self._read_ifd(ifd)
        t = self.tags
        self.width = int(t[TAG_WIDTH][0])
        self.height = int(t[TAG_HEIGHT][0])
        spp = int(t.get(TAG_SPP, [1])[0])
        planar = int(t.get(TAG_PLANAR, [1])[0])
        if spp != 1 and planar != 2:
            raise ValueError('only single-band or band-separate (planar) images are supported')
        self.bands = spp
        bits = int(t.get(TAG_BITS, [8])[0])
        fmt = int(t.get(TAG_SAMPLE_FORMAT, [1])[0])
        self.dtype = _dtype(bits, fmt, self.big_endian)
        self.compression = int(t.get(TAG_COMPRESSION, [1])[0])
        if self.compression not in (1, 5, 8, 32946):
            raise ValueError(f'unsupported compression {self.compression}')
        self.predictor = int(t.get(TAG_PREDICTOR, [1])[0])
        if TAG_TILE_W in t:
            self.block_w = int(t[TAG_TILE_W][0])
            self.block_h = int(t[TAG_TILE_H][0])
            self.offsets = list(t[TAG_TILE_OFFSETS])
            self.counts = list(t[TAG_TILE_BYTES])
        else:
            self.block_w = self.width
            self.block_h = int(t.get(TAG_ROWS_PER_STRIP, [self.height])[0])
            self.offsets = list(t[TAG_STRIP_OFFSETS])
            self.counts = list(t[TAG_STRIP_BYTES])
        self.blocks_across = -(-self.width // self.block_w)
        self.blocks_per_band = self.blocks_across * (-(-self.height // self.block_h))
        scale = t.get(TAG_PIXEL_SCALE)
        tie = t.get(TAG_TIEPOINT)
        if not scale or not tie:
            raise ValueError('missing GeoTIFF georeferencing')
        self.res_x, self.res_y = float(scale[0]), float(scale[1])
        # Tiepoint (i, j, k, x, y, z): raster (i, j) sits at world (x, y).
        self.origin_x = float(tie[3]) - float(tie[0]) * self.res_x
        self.origin_y = float(tie[4]) + float(tie[1]) * self.res_y
        nd = t.get(TAG_NODATA)
        try:
            self.nodata = float(nd.rstrip(b'\x00').decode()) if nd else None
        except (ValueError, AttributeError):
            self.nodata = None

    # ── IFD parsing ──────────────────────────────────────────────────────
    def _bytes(self, offset, length):
        if offset + length <= len(self._head):
            return self._head[offset:offset + length]
        return self._fetch(offset, length)

    def _read_ifd(self, offset):
        e = self._e
        if self.bigtiff:
            n = struct.unpack(e + 'Q', self._bytes(offset, 8))[0]
            entry_size, count_fmt, inline = 20, 'Q', 8
            raw = self._bytes(offset + 8, n * entry_size)
        else:
            n = struct.unpack(e + 'H', self._bytes(offset, 2))[0]
            entry_size, count_fmt, inline = 12, 'I', 4
            raw = self._bytes(offset + 2, n * entry_size)
        tags = {}
        csize = struct.calcsize(count_fmt)
        for i in range(n):
            ent = raw[i * entry_size:(i + 1) * entry_size]
            tag, typ = struct.unpack(e + 'HH', ent[:4])
            count = struct.unpack(e + count_fmt, ent[4:4 + csize])[0]
            size = _TYPE_SIZES.get(typ)
            if size is None:
                continue
            total = size * count
            data = ent[4 + csize:]
            if total > inline:
                ptr = struct.unpack(e + count_fmt, data[:csize])[0]
                data = self._bytes(ptr, total)
            else:
                data = data[:total]
            if typ == 2:
                tags[tag] = bytes(data)
                continue
            f = _TYPE_FMT[typ]
            vals = struct.unpack(e + f * count, data) if len(f) == 1 else \
                struct.unpack(e + f[0] * (2 * count), data)
            if typ in (5, 10):
                vals = tuple(vals[k] / vals[k + 1] if vals[k + 1] else 0.0
                             for k in range(0, len(vals), 2))
            tags[tag] = vals
        return tags

    # ── Block decode ─────────────────────────────────────────────────────
    def _decode_block(self, index):
        raw = self._fetch(int(self.offsets[index]), int(self.counts[index]))
        if self.compression == 5:
            raw = lzw_decode(raw)
        elif self.compression in (8, 32946):
            raw = zlib.decompress(raw)
        bw, bh = self.block_w, self.block_h
        itemsize = self.dtype.itemsize
        need = bw * bh * itemsize
        if len(raw) < need:                 # last strip may be short
            bh = len(raw) // (bw * itemsize)
            need = bw * bh * itemsize
        raw = raw[:need]
        if self.predictor == 3:
            # Floating-point predictor: byte-wise horizontal differencing over
            # rows whose bytes were split into planes, most significant first.
            b = np.frombuffer(raw, np.uint8).reshape(bh, bw * itemsize)
            b = np.cumsum(b, axis=1, dtype=np.uint8)
            b = b.reshape(bh, itemsize, bw).transpose(0, 2, 1)
            kind = self.dtype.kind
            arr = np.ascontiguousarray(b).view(np.dtype(f'>{kind}{itemsize}')).reshape(bh, bw)
            return arr.astype(self.dtype.newbyteorder('='))
        arr = np.frombuffer(raw, self.dtype).reshape(bh, bw)
        if self.predictor == 2:
            # Horizontal differencing is integer arithmetic on the sample
            # words (libtiff applies it to float data bit-for-bit too).
            u = arr.view(np.dtype(f"{'>' if self.big_endian else '<'}u{itemsize}"))
            u = np.cumsum(u.astype(np.dtype(f'u{itemsize}')), axis=1, dtype=np.dtype(f'u{itemsize}'))
            arr = u.view(self.dtype.newbyteorder('='))
        return arr.astype(self.dtype.newbyteorder('='))

    # ── Public API ───────────────────────────────────────────────────────
    def read_rows_cols(self, r0, r1, c0, c1, band: int = 0):
        """Pixel window [r0:r1, c0:c1] of one band (clipped) as float64."""
        r0, c0 = max(0, r0), max(0, c0)
        r1, c1 = min(self.height, r1), min(self.width, c1)
        if r1 <= r0 or c1 <= c0:
            raise ValueError('window outside image')
        out = np.empty((r1 - r0, c1 - c0), dtype=np.float64)
        for br in range(r0 // self.block_h, (r1 - 1) // self.block_h + 1):
            for bc in range(c0 // self.block_w, (c1 - 1) // self.block_w + 1):
                idx = band * self.blocks_per_band + br * self.blocks_across + bc
                blk = self._decode_block(idx)
                y0, x0 = br * self.block_h, bc * self.block_w
                ys, ye = max(r0, y0), min(r1, y0 + blk.shape[0])
                xs, xe = max(c0, x0), min(c1, x0 + blk.shape[1])
                if ye > ys and xe > xs:
                    out[ys - r0:ye - r0, xs - c0:xe - c0] = blk[ys - y0:ye - y0, xs - x0:xe - x0]
        if self.nodata is not None:
            out[out == self.nodata] = np.nan
        out[~np.isfinite(out)] = np.nan
        return out, (r0, c0)

    def read_window(self, lon_min, lat_min, lon_max, lat_max, pad: int = 1):
        """
        Pixels covering a lon/lat box (plus `pad` pixels), as float64 with
        nodata → NaN. Returns (array, (west, north, res_x, res_y)) where
        (west, north) is the outer corner of pixel [0, 0].
        """
        c0 = int(np.floor((lon_min - self.origin_x) / self.res_x)) - pad
        c1 = int(np.ceil((lon_max - self.origin_x) / self.res_x)) + pad
        r0 = int(np.floor((self.origin_y - lat_max) / self.res_y)) - pad
        r1 = int(np.ceil((self.origin_y - lat_min) / self.res_y)) + pad
        arr, (rr, cc) = self.read_rows_cols(r0, r1, c0, c1)
        west = self.origin_x + cc * self.res_x
        north = self.origin_y - rr * self.res_y
        return arr, (west, north, self.res_x, self.res_y)

    def read_all(self):
        arr, _ = self.read_rows_cols(0, self.height, 0, self.width)
        return arr, (self.origin_x, self.origin_y, self.res_x, self.res_y)


def http_fetcher(url: str, session=None, timeout: float = 60.0):
    """A fetch(offset, length) callable backed by HTTP Range requests."""
    import requests
    http = session or requests

    def fetch(offset, length):
        r = http.get(url, headers={'Range': f'bytes={offset}-{offset + length - 1}'},
                     timeout=timeout)
        if r.status_code not in (200, 206):
            raise IOError(f'HTTP {r.status_code} for {url}')
        body = r.content
        if r.status_code == 200:            # server ignored Range
            body = body[offset:offset + length]
        return body
    return fetch


def bytes_fetcher(blob: bytes):
    """A fetch callable over an in-memory file (tests, small downloads)."""
    def fetch(offset, length):
        return blob[offset:offset + length]
    return fetch
