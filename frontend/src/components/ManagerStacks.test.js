import { render, screen } from "@testing-library/react"

import ManagerStacks from "./ManagerStacks"

const manager = (username, maxBid, extra = {}) => ({
    userId: username,
    username,
    profilePic: null,
    teamValue: 100000000,
    balance: 1000000,
    maxBid,
    balanceWithBonuses: 1000000,
    maxBidWithBonuses: maxBid,
    ...extra
})

const BALANCES = [
    manager("Anna", 30000000),
    manager("shirazzi", 20000000, { isSelf: true }),
    manager("Pleite", 0, { balance: -8000000, balanceWithBonuses: -8000000 })
]

describe("ManagerStacks", () => {
    it("lists every manager, richest stack first", () => {
        render(<ManagerStacks balances={BALANCES} />)

        const names = screen.getAllByText(/Anna|shirazzi|Pleite/).map((node) => node.textContent)
        expect(names).toEqual(["Anna", "shirazzi", "Pleite"])
    })

    it("places the user in the field, since that is the point of the panel", () => {
        render(<ManagerStacks balances={BALANCES} />)

        expect(screen.getByText(/Platz 2 von 3/)).toBeTruthy()
    })

    it("says every number is an estimate, and what the calibration measured", () => {
        render(<ManagerStacks balances={BALANCES} />)

        expect(screen.getByText(/Budgets sind Schätzungen/)).toBeTruthy()
        expect(screen.getByText(/204 echte Käufe/)).toBeTruthy()
    })

    it("admits when no manager is marked as the user rather than guessing one", () => {
        // What balances.json looks like until a scrape has run with the flag in place
        render(<ManagerStacks balances={BALANCES.map(({ isSelf, ...rest }) => rest)} />)

        const warning = screen.getByText(/kein Manager als/)
        expect(warning).toBeTruthy()
        expect(screen.getByText(/3 Manager nach geschätztem Maximalgebot/)).toBeTruthy()

        // All three affected columns are named, since all three claim something else than
        // they mean while the flag is missing
        expect(warning.textContent).toMatch(/Verdeckte Bieter/)
        expect(warning.textContent).toMatch(/Mindestgebot/)
        expect(warning.textContent).toMatch(/Zwangsverkauf/)
    })

    it("puts that warning where it can be seen, not inside the collapsed panel", () => {
        // It sat in the AccordionDetails once. The panel is collapsed by default, so the
        // warning was in the DOM, counted as rendered by every test, and read by nobody.
        render(<ManagerStacks balances={BALANCES.map(({ isSelf, ...rest }) => rest)} />)

        const warning = screen.getByText(/kein Manager als/)
        expect(warning.closest(".MuiCollapse-root")).toBeNull()
        expect(warning.closest(".MuiAccordion-root")).toBeNull()
    })

    it("stays quiet once a manager is marked", () => {
        render(<ManagerStacks balances={BALANCES} />)

        expect(screen.queryByText(/kein Manager als/)).toBeNull()
    })

    it("renders nothing at all without balances", () => {
        // A panel of nothing is worse than no panel
        const { container } = render(<ManagerStacks balances={[]} />)
        expect(container.firstChild).toBeNull()
    })
})
