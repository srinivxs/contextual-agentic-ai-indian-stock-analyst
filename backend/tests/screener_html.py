"""Synthetic screener.in-style company pages with NUMBERS, for the fictional DemoCo (P11).

Only the structure is real: the markup of the top-ratios list and of the five data tables, as
screener.in used them when this was written (September 2026). Element ids, classes, the label
spellings ("Sales +", "EPS in Rs", "Borrowing" for a bank), the "Figures in Rs. Crores" caption,
the "TTM" column, percent cells, empty cells, the "Raw PDF" row, Western digit grouping in the
tables and Indian grouping in the market cap. Every number is invented. Real pages, and real
companies' numbers, never enter the repository.

Two pages: DEMOCO_PAGE (an ordinary company, shaped like an IT firm's page) and DEMOBANK_PAGE (a
bank, whose rows are different). Each is built from small helpers that write the same markup the
real page has, so the tests read the tables as rows of plain values.
"""

# A cell is its text as the page shows it; "" is an empty cell.
Row = tuple[str, list[str]]


def _header(columns: list[str]) -> str:
    cells = "".join(
        f"""
          <th class=""
              data-date-key="{column}">
            {column}
          </th>"""
        for column in columns
    )
    return f"""
    <thead>
      <tr>
        <th class="text"></th>{cells}
      </tr>
    </thead>"""


def _label(label: str, section: str) -> str:
    """A row label as the page writes it. "Sales +" is an expandable row: a button whose text ends
    in a non-breaking space and a "+" icon. A plain label is just text."""
    if label.endswith(" +"):
        name = label.removesuffix(" +")
        return f"""
                <button class="button-plain"
                        onclick="Company.showSchedule('{name}', '{section}', this)">
                  {name}&nbsp;<span class="blue-icon">+</span>
                </button>"""
    return f"""
                {label}"""


def _body(rows: list[Row], section: str) -> str:
    html = []
    for label, cells in rows:
        values = "".join(
            f"""
              <td class="">
                  {cell}
              </td>"""
            for cell in cells
        )
        html.append(f"""
          <tr class="stripe">
            <td class="text">{_label(label, section)}
            </td>{values}
          </tr>""")
    return "<tbody>" + "".join(html) + "\n    </tbody>"


def _raw_pdf_row(count: int) -> str:
    """The quarterly table's last row: links to screener's own copies of results, at a path its
    robots.txt disallows (/company/source/quarter/). No numbers; never followed."""
    cells = "".join(
        f"""
            <td class="hover-link">
              <a href="/company/source/quarter/9999/{n}/2026/" target="_blank"
                 aria-label="Raw PDF"><i class="icon-file-pdf ink-600-link"></i></a>
            </td>"""
        for n in range(count)
    )
    return f"""
        <tr>
          <td class="text ink-600">Raw PDF</td>{cells}
        </tr>"""


def _section(
    section: str,
    title: str,
    columns: list[str],
    rows: list[Row],
    *,
    caption: str = "Figures in Rs. Crores",
    raw_pdf: bool = False,
) -> str:
    body = _body(rows, section)
    if raw_pdf:
        body = body.replace("\n    </tbody>", _raw_pdf_row(len(columns)) + "\n    </tbody>")
    return f"""
    <section id="{section}" class="card card-large">
      <div class="flex-row flex-space-between gap-16">
        <div>
          <h2>{title}</h2>
          <p class="sub" style="margin: 0;">
            Consolidated
            {caption}
            /
            <a href="/company/DEMO/#{section}" class="">View Standalone</a>
          </p>
        </div>
      </div>
      <div class="responsive-holder hidden" data-segment-table></div>
      <div class="responsive-holder fill-card-width" data-result-table>
        <table class="data-table responsive-text-nowrap">{_header(columns)}
          {body}
        </table>
      </div>
    </section>"""


def _ratio(name: str, value: str) -> str:
    return f"""
  <li class="flex flex-space-between" data-source="default">
    <span class="name">
        {name}
    </span>
      <span class="nowrap value">{value}</span>
  </li>"""


def _number(text: str) -> str:
    return f'<span class="number">{text}</span>'


def _top_ratios(items: list[tuple[str, str]]) -> str:
    ratios = "".join(_ratio(name, value) for name, value in items)
    return f"""
    <div class="card card-large" id="top">
      <h1>DemoCo</h1>
      <div class="company-ratios">
        <ul id="top-ratios">{ratios}
        </ul>
        <div><label for="quick-ratio-search">Add ratio to table</label></div>
      </div>
    </div>"""


# Things the parser must ignore: a growth summary in "ranges-table"s inside the profit-loss section
# (not a data-table), and a data-table in a section we do not read (shareholding).
_GROWTH_RANGES = """
      <table class="ranges-table">
        <tr><th colspan="2">Compounded Sales Growth</th></tr>
        <tr><td>3 Years:</td><td>11%</td></tr>
        <tr><td>TTM:</td><td>9%</td></tr>
      </table>"""

_SHAREHOLDING = """
    <section id="shareholding" class="card card-large">
      <p class="sub">Numbers in percentages</p>
      <table class="data-table">
        <thead><tr><th class="text"></th><th>Mar 2026</th></tr></thead>
        <tbody>
          <tr><td class="text">Promoters&nbsp;<span class="blue-icon">+</span></td>
              <td>61.20%</td></tr>
          <tr><td class="text">No. of Shareholders</td><td>12,34,567</td></tr>
        </tbody>
      </table>
    </section>"""


def _page(*parts: str) -> str:
    return "<!doctype html>\n<html><body><main>" + "".join(parts) + "\n</main></body></html>\n"


# --- DemoCo: an ordinary company -----------------------------------------------------------------

DEMOCO_TOP_RATIOS = _top_ratios(
    [
        ("Market Cap", f"₹ {_number('1,23,456')} Cr."),
        ("Current Price", f"₹ {_number('3,100')}"),
        ("High / Low", f"₹ {_number('3,500')} / {_number('2,700')}"),
        ("Stock P/E", _number("22.5")),
        ("Book Value", f"₹ {_number('610')}"),
        ("Dividend Yield", f"{_number('1.80')} %"),
        ("ROCE", f"{_number('32.0')} %"),
        ("ROE", f"{_number('25.5')} %"),
        ("Face Value", f"₹ {_number('1.00')}"),
    ]
)

DEMOCO_QUARTERS = _section(
    "quarters",
    "Quarterly Results",
    ["Sep 2025", "Dec 2025", "Mar 2026", "Jun 2026"],
    [
        ("Sales +", ["1,234", "1,310", "1,402", "1,455"]),
        ("Expenses +", ["1,010", "1,070", "1,140", "1,180"]),
        ("OPM %", ["18%", "18%", "19%", "19%"]),
        ("Other Income +", ["-12", "5", "0", "7"]),
        ("Net Profit +", ["210", "225", "", "250"]),
        ("EPS in Rs", ["4.20", "4.50", "4.90", "5.00"]),
    ],
    raw_pdf=True,
)

DEMOCO_PROFIT_LOSS = _section(
    "profit-loss",
    "Profit & Loss",
    ["Mar 2024", "Mar 2025", "Mar 2026", "TTM"],
    [
        ("Sales +", ["4,100", "4,650", "5,120", "5,402"]),
        ("Net Profit +", ["610", "700", "812", "860"]),
        ("EPS in Rs", ["12.20", "14.00", "16.24", "17.20"]),
        ("Dividend Payout %", ["", "25%", "30%", ""]),
    ],
).replace("\n    </section>", _GROWTH_RANGES + "\n    </section>")

# "Sep 2026" is a half-year balance sheet: an annual table's column in a month other than March.
DEMOCO_BALANCE_SHEET = _section(
    "balance-sheet",
    "Balance Sheet",
    ["Mar 2024", "Mar 2025", "Mar 2026", "Sep 2026"],
    [
        ("Equity Capital", ["50", "50", "50", "50"]),
        ("Reserves", ["2,950", "3,450", "", "4,100"]),
        ("Borrowings +", ["820", "1,020", "1,250", "1,300"]),
        ("Other Liabilities +", ["700", "760", "800", "820"]),
        ("Total Liabilities", ["4,520", "5,280", "5,900", "6,270"]),
    ],
)

DEMOCO_CASH_FLOW = _section(
    "cash-flow",
    "Cash Flows",
    ["Mar 2024", "Mar 2025", "Mar 2026"],
    [
        ("Cash from Operating Activity +", ["690", "760", "880"]),
        ("Cash from Investing Activity +", ["-310", "-1,240", "-420"]),
        ("CFO/OP", ["95%", "97%", "101%"]),
    ],
)

# Like the real ratios table of an ordinary company: ROCE %, and no ROE % row.
DEMOCO_RATIOS = _section(
    "ratios",
    "Ratios",
    ["Mar 2024", "Mar 2025", "Mar 2026"],
    [
        ("Debtor Days", ["70", "72", "68"]),
        ("Inventory Days", ["", "", ""]),
        ("ROCE %", ["30%", "31%", "32%"]),
    ],
    caption="",
)

DEMOCO_PAGE = _page(
    DEMOCO_TOP_RATIOS,
    DEMOCO_QUARTERS,
    DEMOCO_PROFIT_LOSS,
    DEMOCO_BALANCE_SHEET,
    DEMOCO_CASH_FLOW,
    DEMOCO_RATIOS,
    _SHAREHOLDING,
)


# --- DemoCo Bank: a bank -------------------------------------------------------------------------

# A bank's page has no dividend yield here (a missing ratio) and a blank P/E (an empty number).
DEMOBANK_TOP_RATIOS = _top_ratios(
    [
        ("Market Cap", f"₹ {_number('98,765')} Cr."),
        ("Current Price", f"₹ {_number('1,450')}"),
        ("High / Low", f"₹ {_number('1,600')} / {_number('1,200')}"),
        ("Stock P/E", _number("")),
        ("Book Value", f"₹ {_number('520')}"),
        ("ROCE", f"{_number('7.10')} %"),
        ("ROE", f"{_number('15.2')} %"),
        ("Face Value", f"₹ {_number('1.00')}"),
    ]
)

DEMOBANK_QUARTERS = _section(
    "quarters",
    "Quarterly Results",
    ["Dec 2025", "Mar 2026", "Jun 2026"],
    [
        ("Revenue +", ["8,100", "8,400", "8,650"]),
        ("Interest", ["4,900", "5,050", "5,200"]),
        ("Expenses +", ["3,400", "3,500", "3,380"]),
        ("Financing Profit", ["-200", "-150", "70"]),
        ("Financing Margin %", ["-2%", "-2%", "1%"]),
        ("Other Income +", ["1,300", "1,420", "1,380"]),
        ("Net Profit +", ["850", "910", "960"]),
        ("EPS in Rs", ["11.30", "12.10", "12.75"]),
        ("Gross NPA %", ["", "", ""]),
        ("Net NPA %", ["", "", ""]),
    ],
    raw_pdf=True,
)

DEMOBANK_PROFIT_LOSS = _section(
    "profit-loss",
    "Profit & Loss",
    ["Mar 2025", "Mar 2026", "TTM"],
    [
        ("Revenue +", ["30,200", "33,050", "33,600"]),
        ("Interest", ["18,300", "19,900", "20,150"]),
        ("Net Profit +", ["3,210", "3,520", "3,640"]),
        ("EPS in Rs", ["42.70", "46.80", "48.40"]),
    ],
)

DEMOBANK_BALANCE_SHEET = _section(
    "balance-sheet",
    "Balance Sheet",
    ["Mar 2025", "Mar 2026"],
    [
        ("Equity Capital", ["75", "75"]),
        ("Reserves", ["24,925", "28,125"]),
        ("Deposits", ["210,000", "232,000"]),
        ("Borrowing", ["31,000", "29,500"]),
        ("Total Liabilities", ["275,000", "299,000"]),
    ],
)

DEMOBANK_RATIOS = _section(
    "ratios",
    "Ratios",
    ["Mar 2025", "Mar 2026"],
    [("ROE %", ["14%", "15%"])],
    caption="",
)

DEMOBANK_PAGE = _page(
    DEMOBANK_TOP_RATIOS,
    DEMOBANK_QUARTERS,
    DEMOBANK_PROFIT_LOSS,
    DEMOBANK_BALANCE_SHEET,
    DEMOBANK_RATIOS,
)
