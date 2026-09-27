# Poindexter trademark: evidence file and filing prep

> **Private.** `docs/brand/` is stripped from the public mirror by
> `scripts/sync-to-github.sh`. Compiled 2026-09-25. Not legal advice.

## Why this file exists

A funded UK company, **Poindexter Labs Ltd**, uses the Poindexter name for
expert AI training-data and annotation services
([poindexterlabs.ai](https://www.poindexterlabs.ai/)). Trademark rights depend
on who used a name first, for what, and where, so this file keeps the dated
evidence of our use in one place.

## Our use of the name (dated, verifiable)

| Date       | Event                                                                                  | Evidence                                                        |
| ---------- | -------------------------------------------------------------------------------------- | --------------------------------------------------------------- |
| 2025-09-30 | Project started as "Co-Founder Agent"                                                  | first commit `6fc109bed`                                        |
| 2025-11-02 | Renamed to **Poindexter** (private repo)                                               | commit `1fddf6cf2`, "Rename 'Co-Founder Agent' to 'Poindexter'" |
| 2026-04-10 | Public repo `Glad-Labs/poindexter` created                                             | GitHub `createdAt` 2026-04-10T02:25:55Z                         |
| 2026-04-11 | First public release **v0.1.0**                                                        | GitHub release, published 2026-04-11T00:21:17Z                  |
| 2026-04-14 | First third-party star (someone outside Glad Labs used the name)                       | stargazer API `starred_at`                                      |
| 2026-05-05 | First gladlabs.io post naming Poindexter; 101 published posts name it as of 2026-09-25 | `posts` table                                                   |
| 2026-09-10 | First PyPI release, `poindexter` 0.0.1                                                 | pypi.org JSON API upload time                                   |
| 2026-09-25 | 16 PyPI releases so far; latest 0.147.0                                                | pypi.org                                                        |

**First use in commerce (US):** the defensible date is **2026-04-11**, the
first public release anyone could download. The 2025-11-02 rename was private
and only shows when the name was adopted.

### Wayback Machine snapshots (captured 2026-09-25)

- https://web.archive.org/web/20260925214924/https://github.com/Glad-Labs/poindexter
- https://web.archive.org/web/20260925215059/https://pypi.org/project/poindexter/
- https://web.archive.org/web/20260925215135/https://gladlabs.mintlify.app/docs/welcome
- https://web.archive.org/web/2026*/www.gladlabs.io (8 captures; earliest 2026-04-04)

Refresh them after major releases: open `https://web.archive.org/save/<url>`.

## Poindexter Labs (the other user of the name)

| Fact                                  | Value                                                                | Source                                                                                         |
| ------------------------------------- | -------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------- |
| Legal entity                          | POINDEXTER LABS LTD, UK company 16640819                             | [Companies House](https://find-and-update.company-information.service.gov.uk/company/16640819) |
| Incorporated                          | **2025-08-11**                                                       | Companies House                                                                                |
| Domain `poindexterlabs.ai` registered | **2025-09-09**                                                       | RDAP                                                                                           |
| Business                              | Expert reasoning data, annotation, evaluations; platform "syncronus" | their site                                                                                     |
| Funding                               | £2M seed, May 2026 (Episode 1, Evertrue, Octopus First Cheque)       | Tech.eu, Dealroom                                                                              |
| US trademark filings                  | **none** found (USPTO search 2026-09-25)                             | tmsearch.uspto.gov                                                                             |
| UK/EU/WIPO filings                    | **none** found (TMview search of GB, EM, WO, 2026-09-25)             | tmdn.org/tmview                                                                                |

**They were first.** Their incorporation (Aug 2025) and domain (Sep 2025) both
predate our adoption (Nov 2025) and our first public use (Apr 2026). Trademark
rights are tied to goods and services, though: they sell data services to AI
labs, we ship content-generation software. Two businesses can hold the same
word for different goods (Hilton holds POINDEXTER for coffee shops alongside
unrelated live POINDEXTER marks). The overlap risk is that both are "AI", and
their name has much more press.

## USPTO register snapshot (2026-09-25)

33 marks contain "Poindexter". None is live for software or AI:

- live: Hilton (Class 43, restaurants), Poindexter LLC FL (T-shirts, plush
  toys, and POINDEXTER'S reg. 7124291, whose Class 42 is AV/lighting
  consulting only), Poindexter Nut Co. (Class 29), Mrs. Poindexter (Class 41),
  John/Old Poindexter whiskey (pending, Class 33)
- dead: POINDEXTER for advertising/marketing (Poindexter Systems Inc.,
  Class 35), healthcare management (Class 35), and others

## UK / EU / WIPO register snapshot (TMview, 2026-09-25)

Five marks contain "Poindexter" across the UK IPO, EUIPO and WIPO. **Only
Hilton's is live, and it is Class 43 (restaurants).** Poindexter LLC held
POINDEXTER in Classes 9 and 42 (and others) in the UK, the EU and via WIPO,
but all three have **expired or ended**. Poindexter Labs has filed nothing in
any of them.

## Filing plan (self-filed, cheapest path)

**Class 9, use-based (Section 1(a)), one class: $350** (USPTO fee schedule
effective 2025-01-19, code 7017). Using ID Manual wording avoids the $200
free-form surcharge.

- **Mark:** POINDEXTER (standard characters, no logo, so it covers any
  styling)
- **Owner:** Glad Labs LLC
- **Identification (ID Manual 009-7406, verbatim):** "Downloadable software
  using artificial intelligence (AI) for writing content based on a theme"
- **Dates of first use / first use in commerce:** 2026-04-11 (v0.1.0 public
  release)
- **Specimen:** a screenshot of the PyPI project page or the GitHub release
  page showing the name POINDEXTER next to the download. The URL and access
  date must be in the screenshot or stated with it. Courts have generally
  treated free public distribution of software as use in commerce (e.g.
  Planetary Motion v. Techsplosion, 11th Cir. 2001); confirm with a clinic.

**Why not Class 42 now:** Class 42 covers hosted software/SaaS
(e.g. 042-3346 "Providing temporary use of online non-downloadable software
using artificial intelligence (AI) for automating {content writing}"). We
don't offer that yet, so it would have to be an intent-to-use (1(b))
application: $350 now plus $150 for the statement of use later. Add it when
a hosted Poindexter exists or money allows.

**Filing steps (Matt does these; they need a USPTO.gov account, ID
verification and payment):**

1. Create a USPTO.gov account and complete identity verification.
2. File the base application in Trademark Center.
3. Paste the ID Manual entry above exactly.
4. Upload the specimen screenshot.
5. Expect a first examiner response in roughly 6–8 months. Watch for an
   Office action, since deadlines are strict.

**Free help:** the USPTO Law School Clinic Certification Program (students
file under supervision, free) and the Trademark Pro Bono Program
(income-qualified). Both are listed on uspto.gov.

**Watch for scam mail:** once filed, the application address is public, and
look-alike "trademark registry" invoices follow. Only uspto.gov is real.

## Already done (2026-09-25)

- `TRADEMARKS.md` policy at repo root (ships to the public mirror); README
  footer links it
- ™ on the first README mention, PyPI description ("Poindexter™ by Glad Labs")
  and the docs welcome page
- Wayback snapshots listed above
