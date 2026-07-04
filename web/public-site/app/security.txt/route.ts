// Legacy top-level /security.txt → canonical /.well-known/security.txt.
// RFC 9116 §3 recommends keeping the root location answering for older
// scanners and humans typing the short path.

import { NextRequest, NextResponse } from 'next/server';

export function GET(request: NextRequest) {
  return NextResponse.redirect(
    new URL('/.well-known/security.txt', request.url),
    308
  );
}
