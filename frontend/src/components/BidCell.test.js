import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import BidCell from "./BidCell"

// A row as MarketTable builds it, reduced to what the cell reads
const row = (overrides) => ({
    playerId: "49",
    marketValue: 1000000,
    price: 1300000,
    ownBid: null,
    suggestedBid: 1180000,
    isOwnListing: false,
    ...overrides
})

const cell = (overrides, props) => render(
    <BidCell row={row(overrides)} growthDays={3} targetDays={3} onEdit={() => {}} {...props} />
)

describe("BidCell at rest", () => {
    it("shows the suggestion when no bid is placed", () => {
        cell()
        expect(screen.getByText("1.180.000 €")).toBeInTheDocument()
    })

    it("shows a placed bid with its surcharge on the market value", () => {
        cell({ ownBid: 1250000 })
        expect(screen.getByText("1.250.000 €")).toBeInTheDocument()
        // 1.250.000 / 1.000.000 - 1 = +25 %
        expect(screen.getByText("(+25 %)")).toBeInTheDocument()
    })

    it("prefers the placed bid over the suggestion", () => {
        cell({ ownBid: 1250000 })
        expect(screen.queryByText("1.180.000 €")).not.toBeInTheDocument()
    })

    it("shows a dash when there is nothing to suggest", () => {
        // Flat or falling market value, or a history too short for the window
        cell({ suggestedBid: null })
        expect(screen.getByText("–")).toBeInTheDocument()
    })

    it("stays clickable without a suggestion", async () => {
        // Declining to recommend must not mean declining to act
        const onEdit = jest.fn()
        cell({ suggestedBid: null }, { onEdit })
        await userEvent.click(screen.getByText("–"))
        expect(onEdit).toHaveBeenCalled()
    })

    it("opens editing when clicked", async () => {
        const onEdit = jest.fn()
        cell({}, { onEdit })
        await userEvent.click(screen.getByText("1.180.000 €"))
        expect(onEdit).toHaveBeenCalled()
    })

    it("renders nothing clickable for an own listing", async () => {
        const onEdit = jest.fn()
        cell({ isOwnListing: true, suggestedBid: 1180000 }, { onEdit })
        expect(screen.queryByText("1.180.000 €")).not.toBeInTheDocument()
        const locked = screen.getByLabelText("Eigenes Angebot")
        await userEvent.click(locked)
        expect(onEdit).not.toHaveBeenCalled()
    })

    it("explains the suggestion in German, naming both horizons", () => {
        cell()
        const hint = screen.getByText("1.180.000 €").closest("[title]")
        expect(hint.getAttribute("title")).toMatch(/3 Tage/)
        expect(hint.getAttribute("title")).toMatch(/Break-Even/)
    })

    it("warns when the suggestion is below the asking price", () => {
        // Valid, but the seller is unlikely to take it
        cell({ suggestedBid: 1100000, price: 1300000 })
        const hint = screen.getByText("1.100.000 €").closest("[title]")
        expect(hint.getAttribute("title")).toMatch(/unter dem Preis/)
    })

    it("does not warn when the suggestion clears the asking price", () => {
        cell({ suggestedBid: 1400000, price: 1300000 })
        const hint = screen.getByText("1.400.000 €").closest("[title]")
        expect(hint.getAttribute("title")).not.toMatch(/unter dem Preis/)
    })
})
