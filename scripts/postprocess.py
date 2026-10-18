"""Inspect images; clean API exports, create squares/contact sheets, verify, and ZIP."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import zipfile
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps


def sha256(file):
    return hashlib.sha256(Path(file).read_bytes()).hexdigest()


def load_json(file):
    return json.loads(Path(file).read_text(encoding='utf-8-sig'))


def color(value):
    if value == 'transparent':
        return (0, 0, 0, 0)
    if not isinstance(value, list) or len(value) not in (3, 4) or any(type(x) is not int or not 0 <= x <= 255 for x in value):
        raise ValueError('background must be an RGB/RGBA array or "transparent"')
    return tuple(value) + ((255,) if len(value) == 3 else ())


def background_for(image, job, plan):
    value = job.get('background', plan.get('background'))
    if value is not None:
        return color(value)
    return (0, 0, 0, 0) if image.getextrema()[3][0] < 255 else (255, 255, 255, 255)


def clean(image, job, background):
    image = image.convert('RGBA')
    polygon = job.get('keep_polygon')
    erasures = job.get('erase_polygons', [])
    if not polygon and not erasures:
        return image
    # Mask coordinates are in the original source, before Photopea crops it.
    if not job.get('box'):
        raise ValueError('Masks require a source box')
    if job.get('script'):
        raise ValueError('Use masks on crop-only jobs; transformed coordinates need a separate plan')
    l, t, r, b = job['box']
    if image.size != (r - l, b - t):
        raise ValueError('Mask geometry does not match exported dimensions')
    mask = Image.new('L', image.size, 0 if polygon else 255)
    draw = ImageDraw.Draw(mask)
    def points(poly):
        if len(poly) < 3 or any(len(p) != 2 or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in p) for p in poly):
            raise ValueError('A polygon needs at least three finite [x,y] points')
        return [(x - l, y - t) for x, y in poly]
    if polygon:
        draw.polygon(points(polygon), fill=255)
    for erase in erasures:
        draw.polygon(points(erase), fill=0)
    return Image.composite(image, Image.new('RGBA', image.size, background), mask)


def source_mae(actual, expected):
    a, b = (np.asarray(im.convert('RGBA'), dtype=float) for im in (actual, expected))
    if a.shape != b.shape:
        raise ValueError('API crop size differs from source crop')
    # Ignore hidden RGB under transparent pixels, and still compare alpha.
    for array in (a, b):
        array[:, :, :3] *= array[:, :, 3:4] / 255
    return float(np.abs(a - b).mean())


def choose_font(requested=None, size=18):
    paths = [requested] if requested else []
    paths += ['C:/Windows/Fonts/msyh.ttc', 'C:/Windows/Fonts/simhei.ttf',
              '/System/Library/Fonts/PingFang.ttc',
              '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',
              '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf']
    for file in paths:
        if file and Path(file).is_file():
            return ImageFont.truetype(file, size)
    return ImageFont.load_default(size=size)


def contact_sheets(files, jobs, output, font_path=None):
    font = choose_font(font_path)
    sheets = []
    # Bound image size for long batches while retaining every expression.
    for start in range(0, len(files), 50):
        subset = files[start:start + 50]
        columns = min(5, len(subset))
        canvas = Image.new('RGB', (columns * 300, math.ceil(len(subset) / columns) * 290), (243, 244, 249))
        draw = ImageDraw.Draw(canvas)
        for i, file in enumerate(subset):
            x, y = (i % columns) * 300, (i // columns) * 290
            draw.rounded_rectangle((x + 8, y + 8, x + 292, y + 282), radius=12, fill='white')
            with Image.open(file) as opened:
                picture = opened.convert('RGBA')
            picture.thumbnail((272, 236), Image.Resampling.LANCZOS)
            canvas.paste(picture, (x + (300 - picture.width) // 2, y + 13 + (236 - picture.height) // 2), picture)
            job = jobs[start + i]
            label = f"{start + i + 1:02d}  {job.get('label', job['name'])}"
            while len(label) > 4 and draw.textlength(label, font=font) > 268:
                label = label[:-2] + '…'
            draw.text((x + 15, y + 254), label, fill=(54, 48, 72), font=font)
        file = output / ('preview.png' if len(files) <= 50 else f'preview_{start // 50 + 1:02d}.png')
        canvas.save(file, optimize=True)
        sheets.append(file)
    return sheets


def package(args):
    source = Path(args.source).resolve()
    plan = load_json(args.plan)
    raw = Path(args.raw).resolve()
    manifest = load_json(raw / 'photopea-manifest.json')
    jobs = plan.get('jobs', [])
    if not jobs or not manifest.get('complete') or len(manifest['jobs']) != len(jobs):
        raise ValueError('A complete Photopea manifest must match every planned job')
    if manifest['source_sha256'] != sha256(source):
        raise ValueError('Source hash differs from the Photopea export')
    out = Path(args.out).resolve()
    archive = out.parent / (out.name + '.zip')
    if not args.overwrite and ((out.exists() and any(out.iterdir())) or archive.exists()):
        raise ValueError('Output exists; use a fresh directory or --overwrite')
    native, square = out / 'original_png', out / 'square_png'
    native.mkdir(parents=True, exist_ok=True)
    if args.square:
        square.mkdir(exist_ok=True)
    with Image.open(source) as opened:
        original = ImageOps.exif_transpose(opened).convert('RGBA')
        original_format = opened.format
    native_files, checks, package_files = [], [], []
    expected_names = {entry['file'] for entry in manifest['jobs']}
    for folder in (native, square):
        if folder.exists() and any(p.name not in expected_names for p in folder.glob('*.png')):
            raise ValueError('Stale images found; choose a fresh delivery directory')
    for job, entry in zip(jobs, manifest['jobs']):
        if job['name'] != entry['name'] or job.get('box') != entry.get('box'):
            raise ValueError('Plan geometry/order differs from the API manifest')
        if entry.get('script', '') != job.get('script', '') or entry.get('shared_script', '') != plan.get('script', ''):
            raise ValueError('Plan editing script differs from the exported operation')
        filename = entry['file']
        if Path(filename).name != filename or '/' in filename or '\\' in filename or not filename.lower().endswith('.png'):
            raise ValueError('Local packaging requires PNG basenames')
        file = raw / filename
        if sha256(file) != entry['sha256']:
            raise ValueError('Raw export hash mismatch: ' + filename)
        with Image.open(file) as opened:
            opened.verify()
        with Image.open(file) as opened:
            image = opened.convert('RGBA')
        if entry.get('size') and list(image.size) != entry['size']:
            raise ValueError('Decoded export dimensions differ from the manifest')
        expected_size = job.get('expected_size')
        if expected_size and list(image.size) != expected_size:
            raise ValueError('Unexpected exported dimensions: ' + filename)
        crop_only = not job.get('script') and not plan.get('script')
        mae = None
        if crop_only and job.get('verify_source', True) and original_format in ('JPEG', 'PNG', 'WEBP', 'BMP', 'TIFF'):
            expected = original.crop(job['box']) if job.get('box') else original
            mae = source_mae(image, expected)
            if mae > args.tolerance:
                raise ValueError(f'API content differs from the source: {filename}, MAE={mae:.4f}')
        if plan.get('script') and (job.get('keep_polygon') or job.get('erase_polygons')):
            raise ValueError('Source-coordinate masks require crop-only jobs')
        background = background_for(image, job, plan)
        cleaned = clean(image, job, background)
        padded = ImageOps.expand(cleaned, border=args.padding, fill=background)
        native_file = native / filename
        padded.save(native_file, optimize=True)
        native_files.append(native_file)
        package_files.append(native_file)
        if args.square:
            ratio = min((args.square - 2 * args.inset) / padded.width, (args.square - 2 * args.inset) / padded.height)
            size = (max(1, round(padded.width * ratio)), max(1, round(padded.height * ratio)))
            resized = padded.resize(size, Image.Resampling.LANCZOS)
            tile = Image.new('RGBA', (args.square, args.square), background)
            # Copy RGBA directly; alpha_composite would apply a second matte.
            tile.paste(resized, ((args.square - size[0]) // 2, (args.square - size[1]) // 2))
            square_file = square / filename
            tile.save(square_file, optimize=True)
            package_files.append(square_file)
        for output_file in package_files[-(2 if args.square else 1):]:
            with Image.open(output_file) as test:
                test.verify()
        checks.append({'name': job['name'], 'raw_size': list(image.size), 'native_size': list(padded.size),
                       'source_mae': None if mae is None else round(mae, 6), 'source_checked': mae is not None,
                       'native_sha256': sha256(native_file)})
    package_files += contact_sheets(native_files, jobs, out, args.font)
    report = out / 'verification.json'
    report.write_text(json.dumps({'count': len(jobs), 'square': args.square, 'jobs': checks}, ensure_ascii=False, indent=2), encoding='utf-8')
    package_files.append(report)
    note = out / '使用说明.txt'
    note.write_text(f'{len(jobs)} 张独立图片。\noriginal_png：原像素尺寸，四周加 {args.padding} 像素留白。\n' +
                    (f'square_png：同一套图片，等比例放入 {args.square}×{args.square} 画布。放大不会增加原图细节。\n' if args.square else '') +
                    '保留图中原有背景与文字。裁剪区域外的透明留白不代表人物背景已经抠除。\n', encoding='utf-8-sig')
    package_files.append(note)
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for file in package_files:
            z.write(file, file.relative_to(out.parent))
    with zipfile.ZipFile(archive) as z:
        if z.testzip() is not None or len(z.namelist()) != len(package_files):
            raise ValueError('Archive verification failed')
    print(json.dumps({'complete': True, 'count': len(jobs), 'zip': str(archive), 'out': str(out),
                      'source_checked': sum(c['source_checked'] for c in checks)}, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    inspect = commands.add_parser('inspect')
    inspect.add_argument('--input', required=True)
    pack = commands.add_parser('package')
    for flag in ('source', 'plan', 'raw', 'out'):
        pack.add_argument('--' + flag, required=True)
    pack.add_argument('--square', type=int, default=512, help='Square size, or 0 to omit')
    pack.add_argument('--padding', type=int, default=8)
    pack.add_argument('--inset', type=int, default=16)
    pack.add_argument('--tolerance', type=float, default=2.0)
    pack.add_argument('--font')
    pack.add_argument('--overwrite', action='store_true')
    args = parser.parse_args()
    if args.command == 'inspect':
        with Image.open(args.input) as image:
            result = {'size': list(image.size), 'oriented_size': list(ImageOps.exif_transpose(image).size),
                      'mode': image.mode, 'format': image.format, 'frames': getattr(image, 'n_frames', 1),
                      'exif_orientation': image.getexif().get(274), 'sha256': sha256(args.input)}
        print(json.dumps(result))
    else:
        if args.padding < 0 or args.square < 0 or args.inset < 0 or (args.square and args.square <= 2 * args.inset):
            parser.error('Require nonnegative padding/inset, and square > 2*inset (or square=0)')
        if not math.isfinite(args.tolerance) or args.tolerance < 0:
            parser.error('tolerance must be finite and nonnegative')
        package(args)


if __name__ == '__main__':
    main()
