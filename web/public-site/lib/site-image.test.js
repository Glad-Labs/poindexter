/**
 * @jest-environment node
 *
 * lib/site-image — the share image's declared format and size have to be the
 * file's real ones.
 *
 * Crawlers lay out a link card from og:image:width/height before they fetch
 * the image. Until 2026-09-28 public/og-image.jpg was a 1024x576 PNG, served
 * as image/jpeg because of its name, while every page declared 1200x630, and
 * nothing compared the two. These tests read the file's header bytes, so a
 * re-export that changes its format or size fails here until the declaration
 * in lib/site-image.js changes with it.
 */
import fs from 'node:fs';
import path from 'node:path';
import {
  SITE_IMAGE,
  siteImageObject,
  siteImageUrl,
  siteOgImage,
} from './site-image';

const SITE_ROOT = path.join(__dirname, '..');
const DECLARING_MODULE = path.join(__dirname, 'site-image.js');

const PNG_SIGNATURE = Buffer.from([
  0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a,
]);

/**
 * Format and pixel size from a PNG's or a JPEG's header bytes.
 *
 * PNG: an 8-byte signature, then the IHDR chunk (length, type, width, height).
 * JPEG: SOI (FF D8), then marker segments up to a start-of-frame, whose
 * payload begins precision(1) height(2) width(2).
 */
function readImageHeader(bytes) {
  if (bytes.subarray(0, 8).equals(PNG_SIGNATURE)) {
    if (bytes.toString('latin1', 12, 16) !== 'IHDR') {
      throw new Error('PNG does not start with an IHDR chunk');
    }
    return {
      type: 'image/png',
      width: bytes.readUInt32BE(16),
      height: bytes.readUInt32BE(20),
    };
  }
  if (bytes[0] === 0xff && bytes[1] === 0xd8) {
    let offset = 2;
    while (offset + 9 <= bytes.length) {
      if (bytes[offset] !== 0xff) {
        throw new Error(`no JPEG marker at byte ${offset}`);
      }
      const marker = bytes[offset + 1];
      if (marker === 0xff) {
        offset += 1; // a fill byte before the marker
        continue;
      }
      // SOF0-SOF15, less the three markers that share the range: DHT (C4),
      // JPG (C8) and DAC (CC).
      const isFrame =
        marker >= 0xc0 &&
        marker <= 0xcf &&
        ![0xc4, 0xc8, 0xcc].includes(marker);
      if (isFrame) {
        return {
          type: 'image/jpeg',
          width: bytes.readUInt16BE(offset + 7),
          height: bytes.readUInt16BE(offset + 5),
        };
      }
      offset += 2 + bytes.readUInt16BE(offset + 2);
    }
    throw new Error('JPEG has no start-of-frame marker');
  }
  // Neither: a new format needs its own branch above before it can pass.
  return { type: 'unknown', width: 0, height: 0 };
}

// Vercel serves a static file with the content type of its extension, and
// that header is what a crawler checks the bytes against.
const SERVED_TYPE = {
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.png': 'image/png',
};

describe('readImageHeader', () => {
  // Positive controls. A reader that returned the declared values for any
  // input would pass every test on the real file, so pin it to real layouts,
  // including the 1024x576 PNG the site image used to be.
  it('reads a PNG header', () => {
    const png = Buffer.alloc(33);
    PNG_SIGNATURE.copy(png, 0);
    png.writeUInt32BE(13, 8);
    png.write('IHDR', 12, 'latin1');
    png.writeUInt32BE(1024, 16);
    png.writeUInt32BE(576, 20);
    expect(readImageHeader(png)).toEqual({
      type: 'image/png',
      width: 1024,
      height: 576,
    });
  });

  it.each([
    ['baseline', 0xc0],
    ['progressive', 0xc2],
  ])('reads a %s JPEG frame past the segments before it', (_kind, sof) => {
    // prettier-ignore
    const jpeg = Buffer.from([
      0xff, 0xd8, // SOI
      0xff, 0xe0, 0x00, 0x10, 0x4a, 0x46, 0x49, 0x46, 0x00, // APP0 "JFIF"
      0x01, 0x01, 0x00, 0x00, 0x01, 0x00, 0x01, 0x00, 0x00,
      0xff, // a fill byte, which may precede any marker
      0xff, 0xc4, 0x00, 0x04, 0x00, 0x00, // DHT: in the SOF range, not a frame
      0xff, sof, 0x00, 0x11, 0x08, // SOFn, 8-bit precision
      0x02, 0x76, 0x04, 0xb0, 0x03, // height 630, width 1200, 3 components
    ]);
    expect(readImageHeader(jpeg)).toEqual({
      type: 'image/jpeg',
      width: 1200,
      height: 630,
    });
  });

  it('does not recognise anything else', () => {
    expect(
      readImageHeader(Buffer.from('<svg xmlns="http://www.w3.org/2000/svg">'))
        .type
    ).toBe('unknown');
  });
});

describe('the site image file', () => {
  const header = readImageHeader(
    fs.readFileSync(path.join(SITE_ROOT, 'public', SITE_IMAGE.url))
  );

  it('is in the format it is declared as', () => {
    expect(header.type).toBe(SITE_IMAGE.type);
  });

  it('is served as the format it is in', () => {
    // A PNG named .jpg goes out as image/jpeg, which is how the old file
    // shipped.
    expect(SERVED_TYPE[path.extname(SITE_IMAGE.url).toLowerCase()]).toBe(
      header.type
    );
  });

  it('is the size it is declared as', () => {
    expect({ width: header.width, height: header.height }).toEqual({
      width: SITE_IMAGE.width,
      height: SITE_IMAGE.height,
    });
  });
});

describe('the site image helpers', () => {
  it('build an openGraph entry with the declared size and format', () => {
    expect(siteOgImage('Alt text')).toEqual({
      url: SITE_IMAGE.url,
      width: SITE_IMAGE.width,
      height: SITE_IMAGE.height,
      type: SITE_IMAGE.type,
      alt: 'Alt text',
    });
  });

  it('build an absolute URL and an ImageObject with the declared size', () => {
    const url = `https://site.example${SITE_IMAGE.url}`;
    expect(siteImageUrl('https://site.example')).toBe(url);
    expect(siteImageObject('https://site.example')).toEqual({
      '@type': 'ImageObject',
      url,
      width: SITE_IMAGE.width,
      height: SITE_IMAGE.height,
    });
  });
});

describe('declarations of the site image', () => {
  // Pages, schemas and feeds read the image from lib/site-image.js, so the
  // declaration tested above is the only one. A file that named the image
  // itself would carry its own copy of the size, free to drift again.
  const SKIP_DIRS = new Set([
    'node_modules',
    '.next',
    'coverage',
    'public',
    'e2e',
    '__tests__',
  ]);
  const SOURCE = /\.(?:[cm]?js|jsx|ts|tsx)$/;
  const TEST = /\.(?:test|spec)\.[cm]?[jt]sx?$/;

  function sourceFiles(dir) {
    return fs.readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
      const file = path.join(dir, entry.name);
      if (entry.isDirectory()) {
        return SKIP_DIRS.has(entry.name) ? [] : sourceFiles(file);
      }
      const isSource =
        entry.isFile() && SOURCE.test(entry.name) && !TEST.test(entry.name);
      return isSource ? [file] : [];
    });
  }

  it('happen only in lib/site-image.js', () => {
    // "og-image" in any extension, so a .png or .webp copy counts too.
    const name = path.posix.parse(SITE_IMAGE.url).name;
    const files = sourceFiles(SITE_ROOT);

    // A scan that read nothing would pass, so check that it reached the tree:
    // the declaring module is in it and the name search finds the declaration.
    expect(files).toContain(DECLARING_MODULE);
    expect(fs.readFileSync(DECLARING_MODULE, 'utf8')).toContain(name);
    expect(files.length).toBeGreaterThan(50);

    const others = files
      .filter(
        (file) =>
          file !== DECLARING_MODULE &&
          fs.readFileSync(file, 'utf8').includes(name)
      )
      .map((file) => path.relative(SITE_ROOT, file));
    expect(others).toEqual([]);
  });
});
