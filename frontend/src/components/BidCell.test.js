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

describe("BidCell while editing", () => {
    const editing = (overrides, props) => render(
        <BidCell
            row={row(overrides)}
            growthDays={3}
            targetDays={3}
            editing
            draft={props?.draft ?? "1180000"}
            onEdit={() => {}}
            onDraftChange={() => {}}
            onSubmit={() => {}}
            onWithdraw={() => {}}
            onCancel={() => {}}
            {...props}
        />
    )

    it("shows the draft in an input with German thousands separators", () => {
        editing()
        expect(screen.getByRole("textbox")).toHaveValue("1.180.000")
    })

    it("submits on the checkmark", async () => {
        const onSubmit = jest.fn()
        editing({}, { onSubmit })
        await userEvent.click(screen.getByLabelText("Gebot abgeben"))
        expect(onSubmit).toHaveBeenCalled()
    })

    it("submits on Enter", async () => {
        const onSubmit = jest.fn()
        editing({}, { onSubmit })
        await userEvent.type(screen.getByRole("textbox"), "{Enter}")
        expect(onSubmit).toHaveBeenCalled()
    })

    it("cancels on the X when no bid is placed", async () => {
        const onCancel = jest.fn()
        const onWithdraw = jest.fn()
        editing({ ownBid: null }, { onCancel, onWithdraw })
        const x = screen.getByLabelText("Abbrechen")
        await userEvent.click(x)
        expect(onCancel).toHaveBeenCalled()
        expect(onWithdraw).not.toHaveBeenCalled()
    })

    it("withdraws on the X when a bid is placed", async () => {
        // Same icon, two meanings - the tooltip and the label say which one applies
        const onCancel = jest.fn()
        const onWithdraw = jest.fn()
        editing({ ownBid: 1250000 }, { onCancel, onWithdraw })
        await userEvent.click(screen.getByLabelText("Gebot zurückziehen"))
        expect(onWithdraw).toHaveBeenCalled()
        expect(onCancel).not.toHaveBeenCalled()
    })

    it("cancels on Escape", async () => {
        const onCancel = jest.fn()
        editing({}, { onCancel })
        await userEvent.type(screen.getByRole("textbox"), "{Escape}")
        expect(onCancel).toHaveBeenCalled()
    })

    it("reports the typed value as digits only", async () => {
        const onDraftChange = jest.fn()
        editing({}, { onDraftChange, draft: "" })
        await userEvent.type(screen.getByRole("textbox"), "1200000")
        expect(onDraftChange).toHaveBeenLastCalledWith("1200000")
    })

    it("disables both actions while a request is in flight", () => {
        render(
            <BidCell
                row={row()} growthDays={3} targetDays={3}
                editing pending draft="1180000"
                onEdit={() => {}} onDraftChange={() => {}} onSubmit={() => {}}
                onWithdraw={() => {}} onCancel={() => {}}
            />
        )
        expect(screen.getByRole("progressbar")).toBeInTheDocument()
        expect(screen.queryByLabelText("Gebot abgeben")).not.toBeInTheDocument()
    })

    it("cannot submit an empty draft", () => {
        editing({}, { draft: "" })
        expect(screen.getByLabelText("Gebot abgeben")).toBeDisabled()
    })
})
