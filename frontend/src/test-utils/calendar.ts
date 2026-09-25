import { screen } from "@testing-library/react";

/** The calendar cell for one day ("September 15, 2026"). Cell labels may carry
 *  extra text ("Today, ", " selected", ", Last available date"), and the hidden
 *  copies of neighbouring months' days are skipped (jsdom applies no CSS). */
export function calendarDay(date: string): HTMLElement {
  const own = new RegExp(`(^|, )\\w+, ${date}( selected)?(,|$)`);
  const [cell] = screen
    .getAllByRole("button")
    .filter((el) => own.test(el.getAttribute("aria-label") ?? "") && !el.dataset.outsideMonth);
  if (!cell) throw new Error(`No calendar cell for ${date}`);
  return cell;
}

/** The calendar's own month arrows (react-aria adds hidden ones too). */
export const nextMonth = () => screen.getAllByRole("button", { name: "Next" })[0];
export const previousMonth = () => screen.getAllByRole("button", { name: "Previous" })[0];

/** Days shown from the neighbouring months must be hidden, not greyed out. */
export function neighbouringDaysAreHidden(): boolean {
  const outside = [...document.querySelectorAll<HTMLElement>("[data-outside-month]")];
  return outside.length > 0 && outside.every((el) => el.classList.contains("invisible"));
}
