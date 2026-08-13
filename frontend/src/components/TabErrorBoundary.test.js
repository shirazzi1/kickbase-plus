import { render, screen } from "@testing-library/react"

import TabErrorBoundary from "./TabErrorBoundary"

// The boundary exists because of something the move to runtime fetching gave up: a
// compile-time import made a broken dataset a build failure, loudly, before anyone saw a page.
// A row with a field the component did not expect now throws inside render - and in React that
// unmounts the whole tree from the root, taking seven working tabs with it.

const Boom = () => {
    throw new Error("Cannot read properties of undefined (reading 'map')")
}

// React logs the caught error itself, which would drown the run in stack traces
let consoleError

beforeEach(() => {
    consoleError = console.error
    console.error = () => {}
})

afterEach(() => {
    console.error = consoleError
})

describe("TabErrorBoundary", () => {
    it("shows what went wrong instead of an empty page", () => {
        render(
            <TabErrorBoundary name="Transfers">
                <Boom />
            </TabErrorBoundary>
        )

        expect(screen.getByText(/Transfers konnte nicht dargestellt werden/)).toBeTruthy()
        // The message, not a shrug: a tab that says "something went wrong" makes a data bug
        // unreportable
        expect(screen.getByText(/reading 'map'/)).toBeTruthy()
    })

    it("says that the other tabs are still fine", () => {
        render(
            <TabErrorBoundary name="Transfers">
                <Boom />
            </TabErrorBoundary>
        )

        expect(screen.getByText(/übrigen Bereiche funktionieren weiter/)).toBeTruthy()
    })

    it("keeps a sibling boundary's content on the page", () => {
        // The whole point: one bad dataset costs one tab
        render(
            <div>
                <TabErrorBoundary name="Transfers"><Boom /></TabErrorBoundary>
                <TabErrorBoundary name="Liga"><span>Tabelle</span></TabErrorBoundary>
            </div>
        )

        expect(screen.getByText("Tabelle")).toBeTruthy()
    })

    it("stays out of the way while nothing is wrong", () => {
        render(<TabErrorBoundary name="Liga"><span>Tabelle</span></TabErrorBoundary>)

        expect(screen.getByText("Tabelle")).toBeTruthy()
        expect(screen.queryByText(/konnte nicht dargestellt werden/)).toBeNull()
    })
})
