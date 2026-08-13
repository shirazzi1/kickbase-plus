// The token the bid endpoints require, and where the browser gets it.
//
// Before this phase it came from `frontend/src/setupProxy.js`: the create-react-app dev
// server attached `X-Bid-Token` to every proxied request from a value in its own environment,
// so the browser never held it and the bundle never contained it. That worked because the dev
// server was also what served the app - including in production, which is the arrangement this
// phase removes. Flask serves the app now, no proxy sits in front of it, and the header had no
// source left: every bid would have answered 401.
//
// So Flask sets a cookie when it serves index.html, holding a token it generates once per
// start, and this reads it back out. See app.py's BOOT_BID_TOKEN for the server half.
//
// **What this protects against.** A page on some other origin cannot read this cookie - the
// same-origin policy covers document.cookie - and cannot send the header either, because
// there is no CORS configuration that would let it. That is what stops a random page the user
// happens to open from spending money in their Kickbase league.
//
// **What it does not.** It is not access control. Anyone who can load the dashboard gets the
// cookie and can bid, exactly as anyone who could reach the dev proxy could bid before.
// Keeping strangers off the port is a separate, still-open piece of work.
//
// In development the dev proxy still attaches its own header from BID_TOKEN, and Flask accepts
// both - so nothing here has to know which of the two setups it is running in.

export const BID_TOKEN_COOKIE = "bid_token"

/**
 * The value of one cookie, or null when it is not set.
 *
 * Written out rather than pulled from a library: it is four lines, and the app has no cookie
 * dependency to add one to.
 */
export function readCookie(name, cookieString = typeof document === "undefined" ? "" : document.cookie) {
    const match = String(cookieString)
        .split(";")
        .map((part) => part.trim())
        .find((part) => part.startsWith(`${name}=`))

    return match ? decodeURIComponent(match.slice(name.length + 1)) : null
}

/**
 * The header to merge into a bid request, or nothing when there is no cookie.
 *
 * An absent cookie yields no header at all rather than an empty one. Both are refused by the
 * server, but a request without the header is the honest description of the situation - and it
 * keeps the dev-proxy setup working, where the proxy adds the header itself and an empty one
 * from here would have to be overwritten.
 */
export function bidTokenHeader() {
    const token = readCookie(BID_TOKEN_COOKIE)

    return token ? { "X-Bid-Token": token } : {}
}
