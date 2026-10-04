import { NextResponse, type NextRequest } from "next/server";

// Once the site is online (SITE_PASSWORD set), the browser asks for a password before any page:
// it can send paper orders. Any user name works. Unset on the local stack.
export function middleware(request: NextRequest) {
  const password = process.env.SITE_PASSWORD;
  if (!password) return NextResponse.next();

  const header = request.headers.get("authorization") ?? "";
  if (header.startsWith("Basic ")) {
    try {
      const decoded = atob(header.slice(6));
      if (decoded.slice(decoded.indexOf(":") + 1) === password) return NextResponse.next();
    } catch {
      // Malformed header: ask again.
    }
  }
  return new NextResponse("Mot de passe requis.", {
    status: 401,
    headers: { "WWW-Authenticate": 'Basic realm="Trading Advisor", charset="UTF-8"' },
  });
}

export const config = { matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"] };
