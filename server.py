#!/usr/bin/env python3
"""
Xtract Server - Static file server + download proxy.
Serves static files and proxies downloads to bypass CORS restrictions.
"""

import http.server
import urllib.request
import urllib.parse
import urllib.error
import os
import sys
import json
import struct
import mimetypes
import io
import re
import gzip
import threading
import tempfile
import subprocess
import shutil
import glob as glob_mod
from concurrent.futures import ThreadPoolExecutor, as_completed
import uuid

# Optional PostgreSQL support
try:
    import psycopg2
    import psycopg2.extras
    HAS_POSTGRES = True
except ImportError:
    HAS_POSTGRES = False

PORT = int(os.environ.get('PORT', 3333))
DATABASE_URL = os.environ.get('DATABASE_URL', '')
DIRECTORY = os.path.dirname(os.path.abspath(__file__))
YTDLP_PATH = shutil.which('yt-dlp') or os.path.expanduser('~/Library/Python/3.9/bin/yt-dlp')
DOWNLOAD_DIR = os.path.join(tempfile.gettempdir(), 'xtract_downloads')

def is_valid_url(url):
    """Basic validation that URL is a real http(s) URL."""
    try:
        parsed = urllib.parse.urlparse(url)
        return parsed.scheme in ('http', 'https') and bool(parsed.hostname)
    except Exception:
        return False


def ensure_jpeg(img_data):
    """Convert image data to JPEG if it's not already (e.g. WebP disguised as .jpg).
    Returns (jpeg_bytes, width, height) or None on failure."""
    # Check if it's already JPEG (starts with FFD8)
    if img_data[:2] == b'\xff\xd8':
        w, h = get_jpeg_dimensions(img_data)
        return img_data, w, h

    # Not JPEG - convert using sips (macOS) or ffmpeg
    tmp_in = tempfile.NamedTemporaryFile(suffix='.webp', delete=False)
    tmp_out = tmp_in.name.replace('.webp', '.jpg')
    try:
        tmp_in.write(img_data)
        tmp_in.close()

        # Try sips first (macOS built-in)
        result = subprocess.run(
            ['sips', '-s', 'format', 'jpeg', tmp_in.name, '--out', tmp_out],
            capture_output=True, timeout=15
        )
        if result.returncode != 0:
            # Fallback to ffmpeg
            result = subprocess.run(
                ['ffmpeg', '-y', '-i', tmp_in.name, '-frames:v', '1', '-update', '1', tmp_out],
                capture_output=True, timeout=15
            )

        if result.returncode == 0 and os.path.exists(tmp_out):
            with open(tmp_out, 'rb') as f:
                jpeg_data = f.read()
            w, h = get_jpeg_dimensions(jpeg_data)
            return jpeg_data, w, h
    except Exception as e:
        sys.stderr.write(f"[Xtract] Image conversion error: {e}\n")
    finally:
        try:
            os.unlink(tmp_in.name)
        except OSError:
            pass
        try:
            os.unlink(tmp_out)
        except OSError:
            pass

    return None


def get_jpeg_dimensions(jpeg_data):
    """Extract width and height from JPEG data."""
    data = io.BytesIO(jpeg_data)
    data.read(2)  # Skip SOI marker
    while True:
        marker = data.read(2)
        if len(marker) < 2:
            break
        if marker[0] != 0xFF:
            break
        m = marker[1]
        if m == 0xD9:  # EOI
            break
        if m == 0xDA:  # SOS - start of scan, stop
            break
        length = struct.unpack('>H', data.read(2))[0]
        if m in (0xC0, 0xC1, 0xC2):  # SOF markers
            data.read(1)  # precision
            height = struct.unpack('>H', data.read(2))[0]
            width = struct.unpack('>H', data.read(2))[0]
            return width, height
        data.read(length - 2)
    return None, None


def build_jpeg_pdf(page_images):
    """Build a minimal valid PDF from a list of JPEG page images.
    page_images: list of {'data': bytes, 'width': int, 'height': int}
    Returns PDF bytes.
    """
    # PDF coordinate system: 72 points per inch
    # We'll scale pages to fit standard US Letter (612x792) while maintaining aspect ratio

    objects = []  # (obj_number, bytes)
    obj_num = [1]  # mutable counter

    def next_obj():
        n = obj_num[0]
        obj_num[0] += 1
        return n

    # Collect all objects first, then write
    catalog_num = next_obj()  # 1
    pages_num = next_obj()    # 2

    page_obj_nums = []
    img_obj_nums = []

    for img_info in page_images:
        page_obj_nums.append(next_obj())
        img_obj_nums.append(next_obj())

    # Build image stream objects and page objects
    page_objects = []
    image_objects = []

    for i, img_info in enumerate(page_images):
        jpeg_data = img_info['data']

        # Try to get actual JPEG dimensions
        jw, jh = get_jpeg_dimensions(jpeg_data)
        if jw and jh:
            img_w, img_h = jw, jh
        else:
            img_w = img_info['width']
            img_h = img_info['height']

        # Scale to fit page (max 612x792 points)
        scale = min(612 / img_w, 792 / img_h)
        page_w = img_w * scale
        page_h = img_h * scale

        # Image XObject
        img_obj = (
            f"{img_obj_nums[i]} 0 obj\n"
            f"<< /Type /XObject /Subtype /Image /Width {img_w} /Height {img_h} "
            f"/ColorSpace /DeviceRGB /BitsPerComponent 8 "
            f"/Filter /DCTDecode /Length {len(jpeg_data)} >>\n"
            f"stream\n"
        ).encode() + jpeg_data + b"\nendstream\nendobj\n"
        image_objects.append((img_obj_nums[i], img_obj))

        # Page content stream: draw image scaled to page
        content = f"q {page_w:.2f} 0 0 {page_h:.2f} 0 0 cm /Img{i} Do Q"
        content_bytes = content.encode()

        content_num = next_obj()

        content_obj = (
            f"{content_num} 0 obj\n"
            f"<< /Length {len(content_bytes)} >>\n"
            f"stream\n"
        ).encode() + content_bytes + b"\nendstream\nendobj\n"

        # Page object
        page_obj = (
            f"{page_obj_nums[i]} 0 obj\n"
            f"<< /Type /Page /Parent {pages_num} 0 R "
            f"/MediaBox [0 0 {page_w:.2f} {page_h:.2f}] "
            f"/Contents {content_num} 0 R "
            f"/Resources << /XObject << /Img{i} {img_obj_nums[i]} 0 R >> >> >>\n"
            f"endobj\n"
        ).encode()

        page_objects.append((page_obj_nums[i], page_obj))
        page_objects.append((content_num, content_obj))

    # Build pages object
    kids = ' '.join(f"{n} 0 R" for n in page_obj_nums)
    pages_obj = (
        f"{pages_num} 0 obj\n"
        f"<< /Type /Pages /Kids [{kids}] /Count {len(page_images)} >>\n"
        f"endobj\n"
    ).encode()

    # Catalog
    catalog_obj = (
        f"{catalog_num} 0 obj\n"
        f"<< /Type /Catalog /Pages {pages_num} 0 R >>\n"
        f"endobj\n"
    ).encode()

    # Assemble all objects
    all_objects = [(catalog_num, catalog_obj), (pages_num, pages_obj)]
    all_objects.extend(page_objects)
    all_objects.extend(image_objects)

    # Sort by object number
    all_objects.sort(key=lambda x: x[0])

    # Write PDF
    output = io.BytesIO()
    output.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")

    offsets = {}
    for obj_n, obj_data in all_objects:
        offsets[obj_n] = output.tell()
        output.write(obj_data)

    # Cross-reference table
    xref_offset = output.tell()
    max_obj = max(offsets.keys())
    output.write(f"xref\n0 {max_obj + 1}\n".encode())
    output.write(b"0000000000 65535 f \n")
    for i in range(1, max_obj + 1):
        if i in offsets:
            output.write(f"{offsets[i]:010d} 00000 n \n".encode())
        else:
            output.write(b"0000000000 00000 f \n")

    # Trailer
    output.write(
        f"trailer\n<< /Size {max_obj + 1} /Root {catalog_num} 0 R >>\n"
        f"startxref\n{xref_offset}\n%%EOF\n".encode()
    )

    return output.getvalue()


def _get_db_conn():
    """Get a PostgreSQL connection. Returns None if DB not configured."""
    if not HAS_POSTGRES or not DATABASE_URL:
        return None
    try:
        return psycopg2.connect(DATABASE_URL)
    except Exception as e:
        sys.stderr.write(f"[Xtract] DB connection error: {e}\n")
        return None

def _init_db():
    """Create users table if it doesn't exist."""
    conn = _get_db_conn()
    if not conn:
        return
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id SERIAL PRIMARY KEY,
                    name VARCHAR(100) NOT NULL,
                    email VARCHAR(255) NOT NULL UNIQUE,
                    purpose TEXT,
                    session_token VARCHAR(64) NOT NULL,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS feedback (
                    id SERIAL PRIMARY KEY,
                    name VARCHAR(100) NOT NULL,
                    email VARCHAR(255) NOT NULL,
                    message TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            """)
            conn.commit()
        sys.stderr.write("[Xtract] Database initialized\n")
    except Exception as e:
        sys.stderr.write(f"[Xtract] DB init error: {e}\n")
    finally:
        conn.close()


class XtractHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DIRECTORY, **kwargs)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)

        if parsed.path == '/api/download':
            self.handle_download(parsed)
        elif parsed.path == '/api/metadata':
            self.handle_metadata(parsed)
        elif parsed.path == '/api/issuu-meta':
            self.handle_issuu_meta(parsed)
        elif parsed.path == '/api/issuu-pdf':
            self.handle_issuu_pdf(parsed)
        elif parsed.path == '/api/extract':
            self.handle_extract(parsed)
        elif parsed.path == '/api/extract-download':
            self.handle_extract_download(parsed)
        elif parsed.path == '/api/scribd-pdf':
            self.handle_scribd_pdf(parsed)
        elif parsed.path == '/api/slideshare-pdf':
            self.handle_slideshare_pdf(parsed)
        elif parsed.path == '/api/search':
            self.handle_search(parsed)
        elif parsed.path == '/api/session':
            self.handle_session(parsed)
        else:
            super().do_GET()

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == '/api/register':
            self.handle_register()
        elif parsed.path == '/api/feedback':
            self.handle_feedback()
        else:
            self.send_error(404, 'Not found')

    def handle_download(self, parsed):
        """Proxy download: fetch remote file and stream it back with download headers."""
        params = urllib.parse.parse_qs(parsed.query)
        url = params.get('url', [None])[0]
        filename = params.get('filename', ['download'])[0]

        if not url:
            self.send_error(400, 'Missing url parameter')
            return

        if not is_valid_url(url):
            self.send_error(400, 'Invalid URL')
            return

        # Properly encode the URL path (spaces -> %20, etc.) while preserving structure
        url = self._encode_url(url)

        # Resolve Archive.org directory URLs to actual file URLs
        url, filename = self._resolve_archive_download(url, filename)

        try:
            parsed_dl = urllib.parse.urlparse(url)
            headers = {
                'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,application/pdf,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.9',
                'Referer': f'{parsed_dl.scheme}://{parsed_dl.netloc}/',
                'Sec-Fetch-Dest': 'document',
                'Sec-Fetch-Mode': 'navigate',
                'Sec-Fetch-Site': 'same-origin',
            }
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=120) as response:
                content_type = response.headers.get('Content-Type', 'application/octet-stream')
                content_length = response.headers.get('Content-Length', '')

                self.send_response(200)
                self.send_header('Content-Type', content_type)
                self.send_header('Content-Disposition', f'attachment; filename="{filename}"')
                if content_length:
                    self.send_header('Content-Length', content_length)
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()

                # Stream in chunks
                while True:
                    chunk = response.read(65536)
                    if not chunk:
                        break
                    self.wfile.write(chunk)

        except urllib.error.HTTPError as e:
            sys.stderr.write(f"[Xtract] Download HTTP error: {e.code} {e.reason} for {url}\n")
            self.send_error(e.code, str(e.reason))
        except Exception as e:
            sys.stderr.write(f"[Xtract] Download error: {str(e)} for {url}\n")
            self.send_error(502, f'Proxy error: {str(e)}')

    @staticmethod
    def _encode_url(url):
        """Encode URL path segments properly, handling spaces and special chars."""
        parsed = urllib.parse.urlparse(url)
        # Re-encode path: decode first (in case partially encoded), then re-encode
        path = urllib.parse.unquote(parsed.path)
        # Encode each path segment individually
        segments = path.split('/')
        encoded_segments = [urllib.parse.quote(seg, safe='') for seg in segments]
        encoded_path = '/'.join(encoded_segments)
        # Reconstruct URL
        return urllib.parse.urlunparse((
            parsed.scheme,
            parsed.netloc,
            encoded_path,
            parsed.params,
            parsed.query,
            parsed.fragment
        ))

    def _resolve_archive_download(self, url, filename):
        """Resolve archive.org/download/{id} directory URLs to actual file URLs."""
        import re
        m = re.match(r'https?://archive\.org/download/([^/]+)/?$', url)
        if not m:
            return url, filename

        identifier = m.group(1)
        # Preferred file formats in order of priority
        preferred_exts = ['.pdf', '.epub', '.djvu', '.txt', '.mp4', '.ogv', '.mp3', '.ogg', '.flac', '.png', '.jpg', '.jpeg', '.gif']
        skip_exts = ['.xml', '.sqlite', '.torrent', '.json', '.txt']  # metadata files
        skip_names = ['__ia_thumb.jpg', '_meta.xml', '_files.xml', '_reviews.xml']

        try:
            meta_url = f'https://archive.org/metadata/{identifier}/files'
            req = urllib.request.Request(meta_url, headers={'User-Agent': 'Mozilla/5.0 Xtract/1.0'})
            with urllib.request.urlopen(req, timeout=15) as response:
                data = json.loads(response.read())

            files = data.get('result', [])
            if not files:
                return url, filename

            # Filter out metadata/derivative files, prefer original sources
            candidates = []
            for f in files:
                name = f.get('name', '')
                source = f.get('source', '')
                fmt = f.get('format', '')
                size = int(f.get('size', 0) or 0)

                if name in skip_names:
                    continue
                if name.startswith('__'):
                    continue
                # Skip private/restricted files
                if f.get('private') == 'true' or f.get('private') is True:
                    continue
                # Skip encrypted/DRM files
                if 'encrypted' in name.lower() or 'Encrypted' in fmt:
                    continue

                ext = ('.' + name.rsplit('.', 1)[-1].lower()) if '.' in name else ''
                candidates.append({
                    'name': name,
                    'ext': ext,
                    'source': source,
                    'format': fmt,
                    'size': size,
                })

            if not candidates:
                return url, filename

            # Score candidates: prefer PDFs, then other document formats, prefer originals, prefer larger files
            def score(c):
                s = 0
                if c['ext'] == '.pdf':
                    s += 1000
                elif c['ext'] in ['.epub', '.djvu']:
                    s += 800
                elif c['ext'] in ['.mp4', '.ogv', '.webm']:
                    s += 700
                elif c['ext'] in ['.mp3', '.ogg', '.flac']:
                    s += 600
                elif c['ext'] in ['.png', '.jpg', '.jpeg', '.gif']:
                    s += 500
                elif c['ext'] in ['.txt']:
                    s += 100
                else:
                    s += 200  # unknown formats still score

                if c['source'] == 'original':
                    s += 100
                if c['size'] > 0:
                    s += min(c['size'] // 10000, 50)  # slight bonus for larger files

                # Penalize very small files (likely metadata)
                if c['size'] < 1000:
                    s -= 500

                return s

            # Sort by score descending, try top candidates with HEAD check
            ranked = sorted(candidates, key=score, reverse=True)
            for candidate in ranked[:5]:
                candidate_url = f'https://archive.org/download/{identifier}/{urllib.parse.quote(candidate["name"])}'
                try:
                    head_req = urllib.request.Request(candidate_url, method='HEAD', headers={
                        'User-Agent': 'Mozilla/5.0 Xtract/1.0',
                        'Referer': 'https://archive.org/',
                    })
                    with urllib.request.urlopen(head_req, timeout=8) as head_resp:
                        if head_resp.status == 200:
                            sys.stderr.write(f"[Xtract] Resolved archive download: {identifier} -> {candidate['name']} ({candidate['size']} bytes)\n")
                            return candidate_url, candidate['name']
                except Exception:
                    sys.stderr.write(f"[Xtract] File not accessible: {candidate['name']}, trying next...\n")
                    continue

            # Fallback: use highest scored without HEAD check
            best = ranked[0]
            resolved_url = f'https://archive.org/download/{identifier}/{urllib.parse.quote(best["name"])}'
            sys.stderr.write(f"[Xtract] Resolved archive download (no HEAD check): {identifier} -> {best['name']}\n")
            return resolved_url, best['name']

        except Exception as e:
            sys.stderr.write(f"[Xtract] Archive resolve error for {identifier}: {e}\n")
            return url, filename

    def handle_metadata(self, parsed):
        """Proxy archive.org metadata API to bypass CORS."""
        params = urllib.parse.parse_qs(parsed.query)
        item_id = params.get('id', [None])[0]

        if not item_id:
            self.send_error(400, 'Missing id parameter')
            return

        # Sanitize item_id (alphanumeric, hyphens, underscores only)
        safe_id = urllib.parse.quote(item_id, safe='-_')
        url = f'https://archive.org/metadata/{safe_id}/files'

        try:
            req = urllib.request.Request(url, headers={
                'User-Agent': 'Mozilla/5.0 Xtract/1.0'
            })
            with urllib.request.urlopen(req, timeout=15) as response:
                data = response.read()

                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(data)

        except Exception as e:
            self.send_error(502, f'Metadata fetch error: {str(e)}')

    def handle_issuu_meta(self, parsed):
        """Fetch Issuu document metadata (page count, image URLs)."""
        params = urllib.parse.parse_qs(parsed.query)
        username = params.get('username', [None])[0]
        slug = params.get('slug', [None])[0]

        if not username or not slug:
            self.send_error(400, 'Missing username or slug parameter')
            return

        url = f'https://reader3.isu.pub/{urllib.parse.quote(username)}/{urllib.parse.quote(slug)}/reader3_4.json'

        try:
            req = urllib.request.Request(url, headers={
                'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
                'Accept': 'application/json',
                'Accept-Encoding': 'gzip',
            })
            with urllib.request.urlopen(req, timeout=20) as response:
                raw = response.read()
                # Decompress if gzipped
                if response.headers.get('Content-Encoding') == 'gzip':
                    raw = gzip.decompress(raw)
                else:
                    try:
                        raw = gzip.decompress(raw)
                    except Exception:
                        pass

                data = json.loads(raw)
                doc = data.get('document', {})
                pages = doc.get('pages', [])

                result = {
                    'title': data.get('title', slug),
                    'pageCount': len(pages),
                    'pages': [{
                        'page': i + 1,
                        'width': p.get('width', 0),
                        'height': p.get('height', 0),
                        'imageUrl': f"https://{p['imageUri']}",
                    } for i, p in enumerate(pages) if not p.get('isPagePaywalled')]
                }

                resp_data = json.dumps(result).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(resp_data)))
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(resp_data)

        except Exception as e:
            sys.stderr.write(f"[Xtract] Issuu meta error: {str(e)}\n")
            self.send_error(502, f'Issuu metadata error: {str(e)}')

    def handle_issuu_pdf(self, parsed):
        """Download all Issuu pages and build a PDF from JPEG images."""
        params = urllib.parse.parse_qs(parsed.query)
        username = params.get('username', [None])[0]
        slug = params.get('slug', [None])[0]

        if not username or not slug:
            self.send_error(400, 'Missing username or slug parameter')
            return

        sys.stderr.write(f"[Xtract] Building PDF for Issuu: {username}/{slug}\n")

        try:
            # 1. Fetch metadata
            meta_url = f'https://reader3.isu.pub/{urllib.parse.quote(username)}/{urllib.parse.quote(slug)}/reader3_4.json'
            req = urllib.request.Request(meta_url, headers={
                'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
                'Accept-Encoding': 'gzip',
            })
            with urllib.request.urlopen(req, timeout=20) as response:
                raw = response.read()
                try:
                    raw = gzip.decompress(raw)
                except Exception:
                    pass
                data = json.loads(raw)

            doc = data.get('document', {})
            pages = [p for p in doc.get('pages', []) if not p.get('isPagePaywalled')]
            title = data.get('title', slug)

            if not pages:
                self.send_error(404, 'No pages found')
                return

            sys.stderr.write(f"[Xtract] Downloading {len(pages)} pages...\n")

            # 2. Download page images
            page_images = []
            for i, page in enumerate(pages):
                img_url = f"https://{page['imageUri']}"
                img_req = urllib.request.Request(img_url, headers={
                    'User-Agent': 'Mozilla/5.0',
                    'Referer': 'https://issuu.com/',
                })
                with urllib.request.urlopen(img_req, timeout=30) as img_resp:
                    img_data = img_resp.read()
                    page_images.append({
                        'data': img_data,
                        'width': page.get('width', 396),
                        'height': page.get('height', 612),
                    })

                if (i + 1) % 50 == 0:
                    sys.stderr.write(f"[Xtract] Downloaded {i + 1}/{len(pages)} pages\n")

            sys.stderr.write(f"[Xtract] All pages downloaded. Building PDF...\n")

            # 3. Build PDF
            pdf_data = build_jpeg_pdf(page_images)

            # 4. Send response
            safe_title = ''.join(c for c in title if c.isalnum() or c in ' -_').strip() or slug
            filename = f"{safe_title}.pdf"

            self.send_response(200)
            self.send_header('Content-Type', 'application/pdf')
            self.send_header('Content-Disposition', f'attachment; filename="{filename}"')
            self.send_header('Content-Length', str(len(pdf_data)))
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(pdf_data)

            sys.stderr.write(f"[Xtract] PDF sent: {filename} ({len(pdf_data)} bytes)\n")

        except Exception as e:
            sys.stderr.write(f"[Xtract] Issuu PDF error: {str(e)}\n")
            self.send_error(502, f'Issuu PDF build error: {str(e)}')

    def handle_extract(self, parsed):
        """Use yt-dlp to extract media info from any URL (Facebook, YouTube, etc.)."""
        params = urllib.parse.parse_qs(parsed.query)
        url = params.get('url', [None])[0]

        if not url or not is_valid_url(url):
            self.send_error(400, 'Missing or invalid url parameter')
            return

        sys.stderr.write(f"[Xtract] Extracting media info: {url}\n")

        try:
            result = subprocess.run(
                [YTDLP_PATH, '--no-warnings', '--cookies-from-browser', 'chrome', '-j', '--no-download', url],
                capture_output=True, text=True, timeout=30
            )

            if result.returncode != 0:
                # Try with --flat-playlist for playlist URLs
                result = subprocess.run(
                    [YTDLP_PATH, '--no-warnings', '--cookies-from-browser', 'chrome', '-j', '--flat-playlist', '--no-download', url],
                    capture_output=True, text=True, timeout=30
                )

            if result.returncode != 0:
                error_msg = result.stderr.strip() or 'yt-dlp could not extract from this URL'
                sys.stderr.write(f"[Xtract] yt-dlp error: {error_msg}\n")
                resp = json.dumps({'error': error_msg, 'supported': False}).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(resp)
                return

            # Parse the JSON output (may have multiple lines for playlists)
            lines = [l for l in result.stdout.strip().split('\n') if l.strip()]
            entries = []
            for line in lines[:20]:  # Limit to 20 entries
                try:
                    info = json.loads(line)
                    entry = {
                        'title': info.get('title', 'Unknown'),
                        'ext': info.get('ext', 'mp4'),
                        'filesize': info.get('filesize') or info.get('filesize_approx') or 0,
                        'format': info.get('format', ''),
                        'duration': info.get('duration', 0),
                        'url': info.get('webpage_url') or info.get('url') or url,
                        'thumbnail': info.get('thumbnail', ''),
                        'type': 'video' if info.get('vcodec', 'none') != 'none' else ('audio' if info.get('acodec', 'none') != 'none' else 'other'),
                    }
                    # List available formats
                    formats = info.get('formats', [])
                    entry['formats'] = [{
                        'format_id': f.get('format_id', ''),
                        'ext': f.get('ext', ''),
                        'quality': f.get('format_note') or f.get('format', ''),
                        'filesize': f.get('filesize') or f.get('filesize_approx') or 0,
                        'vcodec': f.get('vcodec', 'none'),
                        'acodec': f.get('acodec', 'none'),
                    } for f in formats[-10:]]  # Last 10 (usually best quality)
                    entries.append(entry)
                except json.JSONDecodeError:
                    continue

            resp = json.dumps({'supported': True, 'entries': entries}).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(resp)

        except subprocess.TimeoutExpired:
            self.send_error(504, 'Extraction timed out')
        except Exception as e:
            sys.stderr.write(f"[Xtract] Extract error: {str(e)}\n")
            self.send_error(502, f'Extract error: {str(e)}')

    def handle_extract_download(self, parsed):
        """Use yt-dlp to download media from any URL and serve the file."""
        params = urllib.parse.parse_qs(parsed.query)
        url = params.get('url', [None])[0]
        format_id = params.get('format', [None])[0]
        audio_only = params.get('audio', ['false'])[0] == 'true'

        if not url or not is_valid_url(url):
            self.send_error(400, 'Missing or invalid url parameter')
            return

        sys.stderr.write(f"[Xtract] yt-dlp downloading: {url}\n")

        # Create temp dir for this download
        dl_dir = tempfile.mkdtemp(prefix='xtract_')

        try:
            cmd = [YTDLP_PATH, '--no-warnings', '-o', os.path.join(dl_dir, '%(title)s.%(ext)s')]

            # Use browser cookies for private/login-required content
            cmd.extend(['--cookies-from-browser', 'chrome'])

            if audio_only:
                cmd.extend(['-x', '--audio-format', 'mp3'])
            elif format_id:
                cmd.extend(['-f', format_id])
            else:
                # Best quality: merge video+audio with ffmpeg, fallback to single stream
                cmd.extend(['-f', 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best[ext=mp4]/best',
                            '--merge-output-format', 'mp4'])

            cmd.append(url)

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)

            if result.returncode != 0:
                error_msg = result.stderr.strip() or 'Download failed'
                sys.stderr.write(f"[Xtract] yt-dlp download error: {error_msg}\n")
                shutil.rmtree(dl_dir, ignore_errors=True)
                self.send_error(502, error_msg[:200])
                return

            # Find the downloaded file
            downloaded = glob_mod.glob(os.path.join(dl_dir, '*'))
            if not downloaded:
                shutil.rmtree(dl_dir, ignore_errors=True)
                self.send_error(404, 'No file was downloaded')
                return

            filepath = downloaded[0]
            filename = os.path.basename(filepath)
            filesize = os.path.getsize(filepath)
            content_type = mimetypes.guess_type(filepath)[0] or 'application/octet-stream'

            # Sanitize filename for HTTP header (latin-1 safe)
            safe_filename = filename.encode('ascii', 'ignore').decode('ascii')
            safe_filename = ''.join(c for c in safe_filename if c.isalnum() or c in ' .-_()').strip()
            if not safe_filename or not any(c.isalnum() for c in safe_filename):
                safe_filename = 'download' + os.path.splitext(filename)[1]

            sys.stderr.write(f"[Xtract] Serving: {safe_filename} ({filesize} bytes)\n")

            self.send_response(200)
            self.send_header('Content-Type', content_type)
            # Use RFC 5987 for Unicode filename, with ASCII fallback
            encoded_fn = urllib.parse.quote(filename)
            self.send_header('Content-Disposition',
                f'attachment; filename="{safe_filename}"; filename*=UTF-8\'\'{encoded_fn}')
            self.send_header('Content-Length', str(filesize))
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()

            with open(filepath, 'rb') as f:
                while True:
                    chunk = f.read(65536)
                    if not chunk:
                        break
                    self.wfile.write(chunk)

            # Cleanup
            shutil.rmtree(dl_dir, ignore_errors=True)

        except subprocess.TimeoutExpired:
            shutil.rmtree(dl_dir, ignore_errors=True)
            self.send_error(504, 'Download timed out (5 min limit)')
        except Exception as e:
            shutil.rmtree(dl_dir, ignore_errors=True)
            sys.stderr.write(f"[Xtract] Extract-download error: {str(e)}\n")
            self.send_error(502, f'Download error: {str(e)}')

    # --- Scribd helper: find Chrome binary ------------------------------------
    @staticmethod
    def _find_chrome():
        """Return path to Chrome/Chromium binary or None."""
        candidates = [
            '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
            shutil.which('google-chrome'),
            shutil.which('chromium'),
            shutil.which('chromium-browser'),
        ]
        for c in candidates:
            if c and os.path.isfile(c):
                return c
        return None

    def handle_scribd_pdf(self, parsed):
        """Download Scribd document by extracting JSONP page content (text +
        images), building per-page HTML files, rendering each with headless
        Chrome, and compiling the screenshots into a PDF."""
        from PIL import Image as PILImage

        params = urllib.parse.parse_qs(parsed.query)
        doc_id = params.get('doc_id', [None])[0]

        if not doc_id:
            self.send_error(400, 'Missing doc_id parameter')
            return

        sys.stderr.write(f"[Xtract] Scribd PDF build for doc: {doc_id}\n")

        chrome_bin = self._find_chrome()
        if not chrome_bin:
            err = json.dumps({'error': 'Chrome/Chromium not found. Install Google Chrome to enable Scribd downloads.'}).encode()
            self.send_response(501)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(err)
            return

        hdrs = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                          'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
            'Referer': 'https://www.scribd.com/',
        }

        tmp_dir = tempfile.mkdtemp(prefix='xtract_scribd_')

        try:
            # --- Step 1: Fetch the document page HTML -------------------------
            doc_url = f'https://www.scribd.com/document/{doc_id}'
            req = urllib.request.Request(doc_url, headers=hdrs)
            with urllib.request.urlopen(req, timeout=30) as resp:
                page_html = resp.read().decode('utf-8', errors='replace')
            sys.stderr.write(f"[Xtract] Scribd: fetched doc page ({len(page_html)} chars)\n")

            # --- Step 2: Extract JSONP page URLs ------------------------------
            jsonp_urls = re.findall(
                r'https?://html[0-9a-z.-]*\.scribdassets\.com/[a-z0-9]+/pages/\d+-[a-f0-9]+\.jsonp',
                page_html)
            jsonp_urls = list(dict.fromkeys(jsonp_urls))

            if not jsonp_urls:
                err = json.dumps({'error': 'Could not find page data in Scribd document. It may be private or unavailable.'}).encode()
                self.send_response(404)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(err)
                return

            def page_num(u):
                m = re.search(r'/pages/(\d+)-', u)
                return int(m.group(1)) if m else 999
            jsonp_urls.sort(key=page_num)
            sys.stderr.write(f"[Xtract] Scribd: found {len(jsonp_urls)} pages\n")

            # --- Step 3: Fetch all JSONP and extract HTML content -------------
            def fetch_jsonp_html(jsonp_url):
                """Fetch JSONP and return the inner HTML content."""
                r = urllib.request.Request(jsonp_url, headers=hdrs)
                with urllib.request.urlopen(r, timeout=15) as rsp:
                    data = rsp.read()
                try:
                    text = gzip.decompress(data).decode('utf-8', errors='replace')
                except Exception:
                    text = data.decode('utf-8', errors='replace')
                match = re.search(r'window\.page\d+_callback\(\["(.+)"\]\);', text, re.DOTALL)
                if match:
                    h = match.group(1).replace('\\"', '"').replace('\\n', '\n').replace('\\/', '/')
                    h = h.replace('http://html.scribd.com', 'https://html.scribdassets.com')
                    h = h.replace(' orig="', ' src="')
                    return h
                return None

            page_contents = [None] * len(jsonp_urls)
            with ThreadPoolExecutor(max_workers=6) as pool:
                futures = {pool.submit(fetch_jsonp_html, u): i for i, u in enumerate(jsonp_urls)}
                for fut in as_completed(futures):
                    idx = futures[fut]
                    try:
                        page_contents[idx] = fut.result()
                    except Exception as e:
                        sys.stderr.write(f"[Xtract] Scribd: JSONP error page {idx+1}: {e}\n")

            page_contents = [p for p in page_contents if p]
            if not page_contents:
                err = json.dumps({'error': 'Could not extract page content from Scribd.'}).encode()
                self.send_response(502)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(err)
                return

            sys.stderr.write(f"[Xtract] Scribd: extracted {len(page_contents)} page HTMLs\n")

            # --- Step 4: Determine text scale factor --------------------------
            all_content = ''.join(page_contents)
            all_lefts = [int(x) for x in re.findall(r'left:(\d+)px', all_content)]
            max_left = max(all_lefts) if all_lefts else 3000
            PAGE_W, PAGE_H = 902, 1166
            scale = PAGE_W / (max_left + 500)

            # CSS for rendering pages
            page_css = f"""
body {{ margin:0; padding:0; background:white; overflow:hidden; }}
.newpage {{ position:relative; width:{PAGE_W}px; height:{PAGE_H}px; overflow:hidden; background:white; }}
.text_layer {{ position:absolute; top:0; left:0; transform:scale({scale:.6f}); transform-origin:0 0; z-index:2; }}
.image_layer {{ position:absolute; top:0; left:0; z-index:1; }}
.image_layer img, .absimg {{ position:absolute; display:block; }}
.ie_fix {{ position:relative; }}
.a {{ position:absolute; white-space:nowrap; }}
.ff0,.ff1,.ff2,.ff3,.ff4,.ff5,.ff6,.ff7,.ff8 {{ font-family:Arial,Helvetica,sans-serif; }}
.ff0,.ff1,.ff8 {{ font-weight:bold; }}
.ff6 {{ font-weight:bold; }}
"""

            # --- Step 5: Render each page with Chrome -------------------------
            def render_page(args):
                idx, content = args
                html_path = os.path.join(tmp_dir, f'p{idx}.html')
                png_path = os.path.join(tmp_dir, f'p{idx}.png')
                html_doc = f'<!DOCTYPE html><html><head><meta charset="utf-8"><style>{page_css}</style></head><body>{content}</body></html>'
                with open(html_path, 'w') as f:
                    f.write(html_doc)
                subprocess.run([
                    chrome_bin, '--headless=new', '--no-sandbox', '--disable-gpu',
                    f'--screenshot={png_path}',
                    f'--window-size={PAGE_W},{PAGE_H}',
                    '--hide-scrollbars', '--disable-extensions',
                    '--disable-background-networking',
                    f'file://{html_path}'
                ], capture_output=True, timeout=20)
                if os.path.exists(png_path) and os.path.getsize(png_path) > 0:
                    return idx, PILImage.open(png_path).convert('RGB')
                return idx, None

            rendered = [None] * len(page_contents)
            # Run Chrome instances in parallel batches of 4
            with ThreadPoolExecutor(max_workers=4) as pool:
                futures = pool.map(render_page, enumerate(page_contents))
                for idx, img in futures:
                    rendered[idx] = img
                    if img:
                        sys.stderr.write(f"[Xtract] Scribd: rendered page {idx+1}\n")

            pil_images = [img for img in rendered if img is not None]

            if not pil_images:
                err = json.dumps({'error': 'Failed to render Scribd pages.'}).encode()
                self.send_response(502)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(err)
                return

            sys.stderr.write(f"[Xtract] Scribd: rendered {len(pil_images)}/{len(page_contents)} pages, building PDF\n")

            # --- Step 6: Build PDF from rendered pages ------------------------
            pdf_buffer = io.BytesIO()
            pil_images[0].save(pdf_buffer, format='PDF', save_all=True,
                               append_images=pil_images[1:])
            pdf_data = pdf_buffer.getvalue()

            # Extract document title for filename
            title_match = re.search(r'property="og:title"\s+content="([^"]+)"', page_html)
            if title_match:
                import html as html_mod
                raw_title = html_mod.unescape(title_match.group(1))
                doc_title = raw_title.split('|')[0].strip()
            else:
                doc_title = f'scribd_{doc_id}'

            safe_fn = re.sub(r'[^\w\s\-()]', '', doc_title).strip()[:80]
            if not safe_fn:
                safe_fn = f'scribd_{doc_id}'
            filename = f'{safe_fn}.pdf'

            sys.stderr.write(f"[Xtract] Scribd: PDF ready — {len(pdf_data)} bytes, {len(pil_images)} pages\n")

            # --- Step 7: Send response ----------------------------------------
            self.send_response(200)
            self.send_header('Content-Type', 'application/pdf')
            encoded_fn = urllib.parse.quote(filename)
            self.send_header('Content-Disposition',
                f'attachment; filename="{safe_fn}.pdf"; filename*=UTF-8\'\'{encoded_fn}')
            self.send_header('Content-Length', str(len(pdf_data)))
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(pdf_data)

        except urllib.error.HTTPError as e:
            sys.stderr.write(f"[Xtract] Scribd HTTP error: {e.code} {e.reason}\n")
            err = json.dumps({'error': f'Scribd returned HTTP {e.code}. The document may be private or region-restricted.'}).encode()
            self.send_response(502)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(err)
        except Exception as e:
            sys.stderr.write(f"[Xtract] Scribd error: {str(e)}\n")
            err = json.dumps({'error': f'Scribd download failed: {str(e)}'}).encode()
            self.send_response(502)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(err)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def handle_slideshare_pdf(self, parsed):
        """Download SlideShare presentation slides as images and build a PDF.
        SlideShare CDN pattern: image.slidesharecdn.com/{slug}/{quality}/{Title}-{page}-{resolution}.jpg
        /75/ = high-res (2048px), /85/ = low-res (320px)
        """
        params = urllib.parse.parse_qs(parsed.query)
        url = params.get('url', [None])[0]

        if not url:
            self.send_error(400, 'Missing url parameter')
            return

        sys.stderr.write(f"[Xtract] SlideShare PDF for: {url}\n")

        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            }

            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=20) as response:
                html = response.read().decode('utf-8', errors='replace')

            # Extract all slide image URLs from the HTML
            img_urls = re.findall(
                r'(https?://image\.slidesharecdn\.com/[^"\'>\s]+\.(?:jpg|jpeg|png|webp))',
                html
            )

            if not img_urls:
                self.send_error(404, 'Could not extract slide images from SlideShare.')
                return

            # Parse URLs to extract page numbers and build high-res versions
            # Pattern: .../slug/75/Title-{page}-2048.jpg or .../slug/85/Title-{page}-320.jpg
            pages = {}  # page_num -> best URL
            base_pattern = None

            for img_url in img_urls:
                # Extract: everything before the page number, the page num, and resolution
                m = re.match(
                    r'(https://image\.slidesharecdn\.com/[^/]+/)(\d+)/(.+?)-(\d+)-(\d+)\.(jpg|jpeg|png|webp)',
                    img_url
                )
                if not m:
                    continue

                base, quality_dir, title_part, page_num, resolution, ext = m.groups()
                page_num = int(page_num)
                resolution = int(resolution)

                if base_pattern is None:
                    base_pattern = (base, title_part, ext)

                # Keep highest resolution per page
                if page_num not in pages or resolution > pages[page_num][1]:
                    pages[page_num] = (img_url, resolution)

            if not pages and not base_pattern:
                self.send_error(404, 'Could not parse slide image URLs from SlideShare.')
                return

            # If we have a base pattern, try to construct high-res URLs for all pages
            # by using /75/ quality dir and 2048 resolution
            if base_pattern:
                base, title_part, ext = base_pattern
                max_page = max(pages.keys()) if pages else 0

                # Also check HTML for total slide count
                count_match = re.search(r'"totalSlides"\s*:\s*(\d+)', html)
                if not count_match:
                    count_match = re.search(r'of\s+(\d+)\s*</span>', html)
                total_slides = int(count_match.group(1)) if count_match else max_page

                if total_slides > max_page:
                    max_page = total_slides

                # Build high-res URLs for all pages
                for page_num in range(1, max_page + 1):
                    high_res_url = f'{base}75/{title_part}-{page_num}-2048.{ext}'
                    if page_num not in pages or pages[page_num][1] < 2048:
                        pages[page_num] = (high_res_url, 2048)

            # Sort by page number
            sorted_pages = sorted(pages.items())
            sys.stderr.write(f"[Xtract] Downloading {len(sorted_pages)} SlideShare slides\n")

            # Download slide images
            page_images = []
            for page_num, (img_url, _) in sorted_pages:
                img_req = urllib.request.Request(img_url, headers={
                    'User-Agent': headers['User-Agent'],
                    'Referer': 'https://www.slideshare.net/',
                })
                try:
                    with urllib.request.urlopen(img_req, timeout=30) as img_resp:
                        img_data = img_resp.read()
                        if len(img_data) > 1000:
                            # Convert to JPEG if needed (SlideShare serves WebP as .jpg)
                            converted = ensure_jpeg(img_data)
                            if converted:
                                jpeg_data, jw, jh = converted
                                page_images.append({
                                    'data': jpeg_data,
                                    'width': jw or 960,
                                    'height': jh or 720,
                                })
                except urllib.error.HTTPError as e:
                    # If high-res fails, try lower quality
                    if '/75/' in img_url:
                        fallback_url = img_url.replace('/75/', '/85/').replace('-2048.', '-320.')
                        try:
                            fb_req = urllib.request.Request(fallback_url, headers={
                                'User-Agent': headers['User-Agent'],
                                'Referer': 'https://www.slideshare.net/',
                            })
                            with urllib.request.urlopen(fb_req, timeout=30) as fb_resp:
                                fb_data = fb_resp.read()
                                if len(fb_data) > 1000:
                                    converted = ensure_jpeg(fb_data)
                                    if converted:
                                        jpeg_data, jw, jh = converted
                                        page_images.append({
                                            'data': jpeg_data,
                                            'width': jw or 960,
                                            'height': jh or 720,
                                        })
                        except Exception:
                            sys.stderr.write(f"[Xtract] SlideShare slide {page_num} failed\n")
                    else:
                        sys.stderr.write(f"[Xtract] SlideShare slide {page_num} error: {e}\n")

            if not page_images:
                self.send_error(404, 'Failed to download slide images')
                return

            sys.stderr.write(f"[Xtract] Building PDF from {len(page_images)} slides\n")
            pdf_data = build_jpeg_pdf(page_images)

            # Extract title from HTML
            title_match = re.search(r'<title>([^<]+)</title>', html)
            title = title_match.group(1).strip() if title_match else 'slideshare'
            safe_title = ''.join(c for c in title if c.isalnum() or c in ' -_').strip()[:80] or 'slideshare'
            filename = f'{safe_title}.pdf'

            self.send_response(200)
            self.send_header('Content-Type', 'application/pdf')
            safe_fn = filename.encode('ascii', 'ignore').decode('ascii') or 'slideshare.pdf'
            encoded_fn = urllib.parse.quote(filename)
            self.send_header('Content-Disposition',
                f'attachment; filename="{safe_fn}"; filename*=UTF-8\'\'{encoded_fn}')
            self.send_header('Content-Length', str(len(pdf_data)))
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(pdf_data)

        except Exception as e:
            sys.stderr.write(f"[Xtract] SlideShare PDF error: {str(e)}\n")
            self.send_error(502, f'SlideShare download error: {str(e)}')

    # ---- Search Handlers ----

    def handle_search(self, parsed):
        """Aggregate search across free sources: Archive.org, OpenLibrary, YouTube, Wikimedia."""
        params = urllib.parse.parse_qs(parsed.query)
        query = params.get('q', [None])[0]
        media_type = params.get('type', ['all'])[0]
        page = int(params.get('page', ['1'])[0])
        per_page = int(params.get('per_page', ['20'])[0])

        if not query:
            self.send_error(400, 'Missing q parameter')
            return

        sys.stderr.write(f"[Xtract] Search: q={query} type={media_type} page={page}\n")

        # Determine which sources to query
        source_tasks = {}
        allocations = {}

        if media_type in ('all', 'documents'):
            allocations['archive'] = 8 if media_type == 'all' else 12
            allocations['openlibrary'] = 4 if media_type == 'all' else 8
        if media_type in ('all', 'video', 'audio'):
            allocations['archive'] = allocations.get('archive', 0) or (10 if media_type != 'all' else 8)
            allocations['youtube'] = 5 if media_type == 'all' else 10
        if media_type in ('all', 'images'):
            allocations['archive'] = allocations.get('archive', 0) or (10 if media_type != 'all' else 8)
            allocations['wikimedia'] = 3 if media_type == 'all' else 10

        if not allocations:
            allocations = {'archive': 10, 'youtube': 5, 'openlibrary': 3, 'wikimedia': 2}

        results = []
        warnings = []

        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = {}
            if 'archive' in allocations:
                futures[executor.submit(self._search_archive, query, media_type, page, allocations['archive'])] = 'archive'
            if 'openlibrary' in allocations:
                futures[executor.submit(self._search_openlibrary, query, page, allocations['openlibrary'])] = 'openlibrary'
            if 'youtube' in allocations:
                futures[executor.submit(self._search_youtube, query, allocations['youtube'])] = 'youtube'
            if 'wikimedia' in allocations:
                futures[executor.submit(self._search_wikimedia, query, media_type, allocations['wikimedia'], (page - 1) * allocations['wikimedia'])] = 'wikimedia'

            for future in as_completed(futures, timeout=20):
                source_name = futures[future]
                try:
                    source_results = future.result()
                    results.extend(source_results)
                except Exception as e:
                    warnings.append(f"{source_name} search failed: {str(e)[:100]}")
                    sys.stderr.write(f"[Xtract] {source_name} search error: {e}\n")

        # Interleave results from different sources
        source_groups = {}
        for r in results:
            src = r.get('source', 'unknown')
            source_groups.setdefault(src, []).append(r)

        interleaved = []
        source_iters = {k: iter(v) for k, v in source_groups.items()}
        while source_iters:
            exhausted = []
            for src, it in source_iters.items():
                val = next(it, None)
                if val is not None:
                    interleaved.append(val)
                else:
                    exhausted.append(src)
            for src in exhausted:
                del source_iters[src]

        resp = json.dumps({
            'query': query,
            'type': media_type,
            'page': page,
            'per_page': per_page,
            'results': interleaved[:per_page],
            'warnings': warnings,
        }).encode()

        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(resp)))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(resp)

    def _search_archive(self, query, media_type, page, limit):
        """Search Internet Archive."""
        mediatype_map = {
            'documents': 'texts', 'video': 'movies', 'audio': 'audio', 'images': 'image'
        }
        q = query
        if media_type != 'all' and media_type in mediatype_map:
            q += f' AND mediatype:{mediatype_map[media_type]}'
        # Exclude lending-only items (not freely downloadable)
        q += ' AND NOT collection:inlibrary AND NOT collection:lending-library'

        fields = 'identifier,title,description,mediatype,downloads,date,creator'
        url = (
            f'https://archive.org/advancedsearch.php?q={urllib.parse.quote(q)}'
            f'&fl[]={fields.replace(",", "&fl[]=")}'
            f'&rows={limit}&page={page}&output=json'
        )

        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 Xtract/1.0'})
        with urllib.request.urlopen(req, timeout=15) as response:
            data = json.loads(response.read())

        docs = data.get('response', {}).get('docs', [])
        ia_type_map = {'texts': 'documents', 'movies': 'video', 'audio': 'audio', 'image': 'images'}

        results = []
        for doc in docs:
            identifier = doc.get('identifier', '')
            mt = doc.get('mediatype', '')
            desc = doc.get('description', '')
            if isinstance(desc, list):
                desc = desc[0] if desc else ''
            results.append({
                'id': f'ia-{identifier}',
                'title': doc.get('title', identifier),
                'description': (desc or '')[:200],
                'thumbnail': f'https://archive.org/services/img/{identifier}',
                'source': 'archive.org',
                'media_type': ia_type_map.get(mt, 'documents'),
                'url': f'https://archive.org/details/{identifier}',
                'download_url': f'https://archive.org/download/{identifier}',
                'date': doc.get('date', ''),
                'extra': {
                    'downloads': doc.get('downloads', 0),
                    'creator': doc.get('creator', ''),
                },
            })
        return results

    def _search_openlibrary(self, query, page, limit):
        """Search OpenLibrary for books/documents. Only returns books available on Internet Archive."""
        # Request more results since most are lending-only and get filtered out
        fetch_limit = limit * 8
        url = f'https://openlibrary.org/search.json?q={urllib.parse.quote(query)}&page={page}&limit={fetch_limit}'
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 Xtract/1.0'})
        with urllib.request.urlopen(req, timeout=15) as response:
            data = json.loads(response.read())

        docs = data.get('docs', [])
        results = []
        for doc in docs:
            if len(results) >= limit:
                break

            # Only include books available on Internet Archive (actually downloadable)
            ia_ids = doc.get('ia', [])
            if not ia_ids:
                continue

            # Skip non-public / lending-only books (not freely downloadable)
            if doc.get('public_scan_b') is not True:
                continue
            ia_collections = doc.get('ia_collection_s', '')
            if ia_collections and ('inlibrary' in ia_collections or 'printdisabled' in ia_collections):
                continue

            cover_id = doc.get('cover_i')
            thumb = f'https://covers.openlibrary.org/b/id/{cover_id}-M.jpg' if cover_id else None
            key = doc.get('key', '')
            authors = ', '.join(doc.get('author_name', [])[:3])

            download_url = f'https://archive.org/download/{ia_ids[0]}'
            item_url = f'https://archive.org/details/{ia_ids[0]}'

            results.append({
                'id': f'ol-{key}',
                'title': doc.get('title', 'Unknown'),
                'description': authors,
                'thumbnail': thumb,
                'source': 'openlibrary',
                'media_type': 'documents',
                'url': item_url,
                'download_url': download_url,
                'date': str(doc.get('first_publish_year', '')),
                'extra': {
                    'pages': doc.get('number_of_pages_median'),
                },
            })
        return results

    def _search_youtube(self, query, limit):
        """Search YouTube via yt-dlp."""
        cmd = [YTDLP_PATH, '--no-warnings', '--flat-playlist', '--dump-json',
               f'ytsearch{limit}:{query}']
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)

        if result.returncode != 0:
            raise Exception(f'yt-dlp search failed: {result.stderr[:100]}')

        results = []
        for line in result.stdout.strip().split('\n'):
            if not line.strip():
                continue
            try:
                info = json.loads(line)
                vid_id = info.get('id', '')
                thumb = info.get('thumbnail') or (info.get('thumbnails', [{}])[-1].get('url') if info.get('thumbnails') else None) or (f'https://i.ytimg.com/vi/{vid_id}/hqdefault.jpg' if vid_id else None)
                yt_url = info.get('url') or info.get('webpage_url') or f'https://youtube.com/watch?v={vid_id}'
                results.append({
                    'id': f'yt-{vid_id}',
                    'title': info.get('title', 'Unknown'),
                    'description': info.get('description', '')[:200] if info.get('description') else '',
                    'thumbnail': thumb,
                    'source': 'youtube',
                    'media_type': 'video',
                    'url': yt_url,
                    'download_url': yt_url,
                    'date': info.get('upload_date', ''),
                    'extra': {
                        'duration': info.get('duration'),
                        'views': info.get('view_count'),
                    },
                })
            except json.JSONDecodeError:
                continue
        return results

    def _search_wikimedia(self, query, media_type, limit, offset):
        """Search Wikimedia Commons."""
        url = (
            f'https://commons.wikimedia.org/w/api.php?action=query&list=search'
            f'&srsearch={urllib.parse.quote(query)}&srnamespace=6'
            f'&srlimit={limit}&sroffset={offset}&format=json'
        )
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 Xtract/1.0'})
        with urllib.request.urlopen(req, timeout=15) as response:
            data = json.loads(response.read())

        search_results = data.get('query', {}).get('search', [])
        if not search_results:
            return []

        # Batch fetch file info
        titles = '|'.join(r['title'] for r in search_results)
        info_url = (
            f'https://commons.wikimedia.org/w/api.php?action=query&titles={urllib.parse.quote(titles)}'
            f'&prop=imageinfo&iiprop=url|size|mime|mediatype&iiurlwidth=300&format=json'
        )
        info_req = urllib.request.Request(info_url, headers={'User-Agent': 'Mozilla/5.0 Xtract/1.0'})
        with urllib.request.urlopen(info_req, timeout=15) as response:
            info_data = json.loads(response.read())

        pages = info_data.get('query', {}).get('pages', {})
        mime_type_map = {'image': 'images', 'audio': 'audio', 'video': 'video'}

        results = []
        for page_id, page_data in pages.items():
            if page_id == '-1':
                continue
            ii = (page_data.get('imageinfo') or [{}])[0]
            mime = ii.get('mime', '')
            wm_media = mime.split('/')[0]

            # Filter by media type
            mapped = mime_type_map.get(wm_media, 'images')
            if media_type != 'all' and mapped != media_type:
                continue

            title = page_data.get('title', '').replace('File:', '')
            thumb = ii.get('thumburl') or ii.get('url')
            results.append({
                'id': f'wm-{page_id}',
                'title': title,
                'description': '',
                'thumbnail': thumb,
                'source': 'wikimedia',
                'media_type': mapped,
                'url': f"https://commons.wikimedia.org/wiki/{urllib.parse.quote(page_data.get('title', ''))}",
                'download_url': ii.get('url'),
                'date': '',
                'extra': {
                    'size': ii.get('size'),
                },
            })
        return results

    def handle_session(self, parsed):
        """Check if a session token is valid."""
        params = urllib.parse.parse_qs(parsed.query)
        token = params.get('token', [None])[0]

        if not HAS_POSTGRES or not DATABASE_URL:
            # No DB configured — tell frontend to skip modal
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps({'valid': True, 'no_db': True}).encode())
            return

        if not token:
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps({'valid': False}).encode())
            return

        conn = _get_db_conn()
        if not conn:
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps({'valid': True, 'no_db': True}).encode())
            return

        try:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM users WHERE session_token = %s", (token,))
                row = cur.fetchone()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps({'valid': row is not None}).encode())
        except Exception as e:
            sys.stderr.write(f"[Xtract] Session check error: {e}\n")
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps({'valid': True, 'no_db': True}).encode())
        finally:
            conn.close()

    def handle_register(self):
        """Register a new user and return a session token."""
        if not HAS_POSTGRES or not DATABASE_URL:
            self.send_response(503)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps({'error': 'Database not configured'}).encode())
            return

        try:
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length)
            data = json.loads(body)
        except Exception:
            self.send_error(400, 'Invalid JSON')
            return

        name = (data.get('name') or '').strip()
        email = (data.get('email') or '').strip().lower()
        purpose = (data.get('purpose') or '').strip()

        if not name or not email:
            self.send_response(400)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps({'error': 'Name and email are required'}).encode())
            return

        conn = _get_db_conn()
        if not conn:
            self.send_response(503)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps({'error': 'Database unavailable'}).encode())
            return

        try:
            session_token = uuid.uuid4().hex
            with conn.cursor() as cur:
                # Try insert, on duplicate email return existing token
                cur.execute(
                    "SELECT session_token FROM users WHERE email = %s", (email,)
                )
                existing = cur.fetchone()
                if existing:
                    session_token = existing[0]
                else:
                    cur.execute(
                        "INSERT INTO users (name, email, purpose, session_token) VALUES (%s, %s, %s, %s)",
                        (name, email, purpose, session_token)
                    )
                    conn.commit()
                    sys.stderr.write(f"[Xtract] New user registered: {email}\n")

            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps({'session_token': session_token}).encode())
        except Exception as e:
            sys.stderr.write(f"[Xtract] Register error: {e}\n")
            self.send_response(500)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps({'error': 'Registration failed'}).encode())
        finally:
            conn.close()

    def handle_feedback(self):
        """Handle feedback/suggestion submission. Placeholder for GoHighLevel integration."""
        try:
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length)
            data = json.loads(body)
        except Exception:
            self.send_error(400, 'Invalid JSON')
            return

        name = (data.get('name') or '').strip()
        email = (data.get('email') or '').strip()
        message = (data.get('message') or '').strip()

        if not name or not email or not message:
            self.send_response(400)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps({'error': 'All fields are required'}).encode())
            return

        # Store in DB if available
        conn = _get_db_conn()
        if conn:
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO feedback (name, email, message) VALUES (%s, %s, %s)",
                        (name, email, message)
                    )
                    conn.commit()
                sys.stderr.write(f"[Xtract] Feedback saved from {name} <{email}>\n")
            except Exception as e:
                sys.stderr.write(f"[Xtract] Feedback DB error: {e}\n")
            finally:
                conn.close()
        else:
            sys.stderr.write(f"[Xtract] Feedback (no DB) from {name} <{email}>: {message[:100]}\n")

        # TODO: Forward to GoHighLevel webhook here
        # e.g. requests.post(GHL_WEBHOOK_URL, json={...})

        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps({'success': True}).encode())

    def do_OPTIONS(self):
        """Handle CORS preflight for POST requests."""
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def end_headers(self):
        # Add CORS headers and no-cache for dev
        if '/api/' not in self.path:
            self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
        super().end_headers()

    def log_message(self, format, *args):
        # Cleaner logging
        sys.stderr.write(f"[Xtract] {args[0]}\n")


if __name__ == '__main__':
    _init_db()
    print(f"Xtract server running at http://localhost:{PORT}")
    server = http.server.HTTPServer(('', PORT), XtractHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped.")
        server.server_close()
