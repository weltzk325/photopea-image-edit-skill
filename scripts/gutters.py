"""Refine agent-provided vertical gutter guides into crop masks, not object detection."""
import argparse
import json
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw, ImageOps


def interpolate(value, yy, width):
    if isinstance(value, (int, float)):
        result = np.full(len(yy), float(value))
    else:
        points = np.asarray(value, dtype=float)
        if points.ndim != 2 or points.shape[1] != 2 or len(points) < 2:
            raise ValueError('A guide needs two or more [y, x] anchors')
        if not np.isfinite(points).all() or np.any(np.diff(points[:, 0]) <= 0):
            raise ValueError('Guide y coordinates must increase and be finite')
        result = np.interp(yy, points[:, 0], points[:, 1])
    if not np.isfinite(result).all() or np.any(result < 0) or np.any(result > width):
        raise ValueError('Guide outside source width')
    return result


def trace(rgb, top, bottom, anchors, radius=12):
    yy = np.arange(top, bottom)
    guide = interpolate(anchors, yy, rgb.shape[1])
    lo = max(0, int(guide.min()) - radius)
    hi = min(rgb.shape[1] - 1, int(guide.max()) + radius)
    xs = np.arange(lo, hi + 1)
    dark = np.maximum(250 - rgb[top:bottom, lo:hi + 1].astype(float).min(2), 0)
    base = (dark / 24) ** 3
    padded = np.pad(base, ((0, 0), (1, 1)), mode='edge')
    base = np.maximum.reduce([base, padded[:, :-2] * .55, padded[:, 2:] * .55])
    cost = base + .012 * (xs[None, :] - guide[:, None]) ** 2
    cost[np.abs(xs[None, :] - guide[:, None]) > radius] = 1e12
    dp = np.empty_like(cost)
    previous = np.zeros(cost.shape, dtype=np.int32)
    dp[0] = cost[0]
    for y in range(1, len(yy)):
        for col in range(len(xs)):
            left, right = max(0, col - 3), min(len(xs), col + 4)
            choices = dp[y - 1, left:right] + .25 * np.abs(np.arange(left, right) - col)
            p = left + int(np.argmin(choices))
            dp[y, col] = cost[y, col] + choices[p - left]
            previous[y, col] = p
    result = np.empty(len(yy), dtype=int)
    col = int(np.argmin(dp[-1]))
    for y in range(len(yy) - 1, -1, -1):
        result[y] = xs[col]
        col = previous[y, col]
    return result


def build(source, layout):
    image = ImageOps.exif_transpose(Image.open(source)).convert('RGB')
    rgb = np.asarray(image)
    width, height = image.size
    plan = {'source_size': [width, height], 'format': 'png', 'jobs': []}
    if 'background' in layout:
        plan['background'] = layout['background']
    annotated = image.copy()
    draw = ImageDraw.Draw(annotated)
    rows = layout.get('rows', [])
    if not rows:
        raise ValueError('Layout requires a non-empty rows array')
    for row in rows:
        top, bottom = row['top'], row['bottom']
        if not isinstance(top, int) or not isinstance(bottom, int) or not 0 <= top < bottom <= height:
            raise ValueError('Row bounds must be integer pixels inside the source')
        yy = np.arange(top, bottom)
        radius = row.get('radius', 12)
        if not isinstance(radius, int) or not 1 <= radius <= 100:
            raise ValueError('Guide radius must be 1..100 pixels')
        seams = [trace(rgb, top, bottom, guide, radius) for guide in row.get('guides', [])]
        boundaries = [np.rint(interpolate(row.get('left', 0), yy, width)).astype(int)] + seams
        boundaries += [np.rint(interpolate(row.get('right', width), yy, width)).astype(int)]
        names = row['names']
        if len(names) != len(boundaries) - 1:
            raise ValueError('Each row needs one more name than internal guides')
        for index, name in enumerate(names):
            left, right = boundaries[index], boundaries[index + 1]
            if np.any(left >= right):
                raise ValueError('Gutters cross for ' + name)
            l, r = int(left.min()), int(right.max())
            polygon = [[int(x), int(y)] for x, y in zip(left, yy)]
            polygon += [[int(x) - 1, int(y)] for x, y in zip(right[::-1], yy[::-1])]
            plan['jobs'].append({'name': name, 'box': [l, top, r, bottom],
                                 'keep_polygon': polygon, 'expected_size': [r - l, bottom - top]})
        for seam in boundaries:
            draw.line([(int(x), int(y)) for x, y in zip(seam, yy)], fill='#ef3767', width=1)
    return plan, annotated


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True)
    parser.add_argument('--layout', required=True)
    parser.add_argument('--out', required=True)
    parser.add_argument('--preview')
    args = parser.parse_args()
    layout = json.loads(Path(args.layout).read_text(encoding='utf-8-sig'))
    plan, annotated = build(args.input, layout)
    target = Path(args.out)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding='utf-8')
    if args.preview:
        annotated.save(args.preview)
    print(json.dumps({'jobs': len(plan['jobs']), 'plan': str(target)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
