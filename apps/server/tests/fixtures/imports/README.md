# QT-006 import fixtures

These bounded, synthetic task fixtures are QT-006's schema-specific adaptation of
the canonical DATA-001 source-fixture contract. DATA-001 explicitly defines its Notion
page as the schema-independent contract and requires later QT-006 work to adapt those
scenarios to the actual importer rather than freeze its source columns. The repository
fixtures therefore consume the page's canonical SYN-* namespace, business meanings and
acceptance checklist directly; attachment byte-for-byte identity is not an importer
requirement.

Contract mapping used here:
- canonical customers: `SYN-CUST-100`, `SYN-CUST-200`;
- canonical products: `SYN-SKU-VALVE-DN50-BRASS` and `SYN-SKU-TAPE-24MM`;
- canonical book: `SYN-PB-STD-V1`;
- exact tier boundary: brass-valve tiers meet at quantity 10;
- product-specific UOM: tape uses the documented `BOX -> EA` factor 12 integration case;
- inventory shortage evidence: brass-valve inventory is 8 EA;
- explicit decimal/effective-window/reference failures are covered by integration tests,
  while parser-specific malformed-file cases remain QT-006-owned.

Import the valid CSVs in this order: customers, products, pricebooks, prices,
inventory. Map each header to the same-named field. All rows belong to the signed-in
tenant; reuse the same files under a second tenant to prove isolation.

- `customers.csv`: `external_key` is the stable case-sensitive customer key; `name`
  is the display name. Two synthetic customers.
- `customers.xlsx`: the same two customers in a real single-sheet XLSX workbook,
  generated and reopened with openpyxl for verification. All source cells are text.
  Used by both parser and real-browser import tests; openpyxl is not an app dependency.
- `products.csv`: `sku` is the stable case-sensitive SKU; `name` and `description`
  identify the synthetic brass valve and tape. No price is implied by catalog text.
- `pricebooks.csv`: `key` and positive integer `version` identify a book;
  `valid_from` / optional `valid_to` define its half-open UTC authority window.
- `prices.csv`: SKU and exact book key/version are foreign references; `uom` is
  explicit, `unit_price` is exact SGD decimal text, quantity bounds are inclusive
  lower / exclusive upper. Blank upper bound is unbounded. Valve tiers meet at 10.
  Dates define half-open price windows; blank end means open-ended.
- `inventory.csv`: SKU, explicit UOM, signed exact `quantity`, and timezone-aware
  `observed_at` are inventory evidence. The server records actual import time.
  Valve stock of 8 supports a later shortage scenario. Import does not refresh age.
- `invalid_customers.csv`: rows 3, 4, 5 must report respectively missing external
  key, duplicate external key, and missing name; the entire file is rejected.

Parser tests build small XLSX equivalents in memory (including exact decimal XML),
malformed archives, formulas, external links and expansion attacks. Integration
tests add unknown references, history-preserving replacement, overlapping windows,
invalid decimals and future evidence. Values are test examples, not tenant defaults.
