/**
 * The site's own share image, public/og-image.jpg.
 *
 * It is the og:image for every page without a picture of its own, the stand-in
 * for a post with no cover image, and the publisher logo in the JSON-LD.
 * Crawlers (Facebook, LinkedIn, X) lay out a link card from the declared width
 * and height before they fetch the file, so these have to be the file's real
 * numbers. Until 2026-09-28 the file was a 1024x576 PNG under a .jpg name,
 * served as image/jpeg, while every page declared 1200x630.
 *
 * Name the image only through this module. lib/site-image.test.js reads the
 * file's header bytes and fails when its format or size differs from what is
 * declared here, or when another source file names the image itself.
 */
import { SITE_URL } from './site.config';

export const SITE_IMAGE = Object.freeze({
  url: '/og-image.jpg',
  width: 1200,
  height: 630,
  type: 'image/jpeg',
});

/**
 * An openGraph.images entry for the site image. The URL is site-relative, and
 * Next resolves it against metadataBase (app/layout.js).
 *
 * @param {string} alt
 */
export function siteOgImage(alt) {
  return { ...SITE_IMAGE, alt };
}

/** The site image's absolute URL, for JSON-LD and the RSS channel image. */
export function siteImageUrl(siteUrl = SITE_URL) {
  return `${siteUrl}${SITE_IMAGE.url}`;
}

/** The site image as a schema.org ImageObject, with its real size. */
export function siteImageObject(siteUrl = SITE_URL) {
  return {
    '@type': 'ImageObject',
    url: siteImageUrl(siteUrl),
    width: SITE_IMAGE.width,
    height: SITE_IMAGE.height,
  };
}
