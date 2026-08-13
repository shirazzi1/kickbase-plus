const { createProxyMiddleware } = require("http-proxy-middleware")

// CRA loads this file itself when it exists, instead of the "proxy" string in
// package.json - that string has been removed in favour of this file.
//
// The bid endpoints require the X-Bid-Token header (see app.py's _check_bid_token()).
// It is attached here, server-side in the dev server process, rather than in frontend
// code: any value baked into the React bundle (e.g. a REACT_APP_* variable) ships to
// every browser that loads the page and can be read out of it, which would make the
// token a secret in name only. Reading process.env.BID_TOKEN here means it never leaves
// this process - the browser sends no token at all, and the proxy adds it before the
// request reaches Flask on the configured port.
//
// The target is 127.0.0.1, not localhost: on macOS "localhost" can resolve to ::1
// first, where the AirPlay Receiver already answers on port 5000 with a 403 - a
// pre-existing trap the old "proxy" string in package.json had too, not something new
// here. AirPlay Receiver does not just answer there, it also occupies port 5000 by
// default, so Flask cannot even bind to it - that is what FLASK_PORT is for: it lets
// the port be moved without touching this file.
const flaskPort = process.env.FLASK_PORT || 5000

module.exports = function (app) {
    app.use(
        "/api",
        createProxyMiddleware({
            target: `http://127.0.0.1:${flaskPort}`,
            changeOrigin: true,
            onProxyReq: (proxyReq) => {
                proxyReq.setHeader("X-Bid-Token", process.env.BID_TOKEN || "")
            }
        })
    )
}
