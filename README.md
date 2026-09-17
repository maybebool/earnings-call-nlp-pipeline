# earnings-call-nlp-pipeline
[![Python](https://img.shields.io/badge/Python-3.12+-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-18-4169E1?style=flat-square&logo=postgresql&logoColor=white)](https://www.postgresql.org/)
[![SQLAlchemy](https://img.shields.io/badge/SQLAlchemy-2.0-D71F00?style=flat-square&logo=sqlalchemy&logoColor=white)](https://www.sqlalchemy.org/)
[![Alembic](https://img.shields.io/badge/Alembic-Migrations-6BA81E?style=flat-square)](https://alembic.sqlalchemy.org/)
[![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?style=flat-square&logo=docker&logoColor=white)](https://docs.docker.com/compose/)
[![Beautiful Soup](https://img.shields.io/badge/BeautifulSoup-HTML_Parsing-4B8BBE?style=flat-square)](https://www.crummy.com/software/BeautifulSoup/)
[![pdfplumber](https://img.shields.io/badge/pdfplumber-PDF_Extraction-D14836?style=flat-square)](https://github.com/jsvine/pdfplumber)
[![syntok](https://img.shields.io/badge/syntok-Sentence_Segmentation-8A4FFF?style=flat-square)](https://github.com/fnl/syntok)
[![SEC EDGAR](https://img.shields.io/badge/SEC_EDGAR-Filings-1F4E79?style=flat-square)](https://www.sec.gov/edgar)
[![Licence](https://img.shields.io/badge/Licence-MIT-lightgrey?style=flat-square)](LICENSE)

Pipeline for a Bank of England employer project: earnings call transcripts and
reported key figures of UBS Group AG and JPMorgan Chase & Co. from 1Q23 to
4Q24, turned into a PostgreSQL database and into CSV releases for analysis.

Both banks absorbed a failed competitor in 2023. UBS and Credit Suisse was a
merger of two globally systemic banks; JPMorgan and First Republic was an
acquisition out of an FDIC receivership at roughly a tenth of that relative
size. The pair is deliberately asymmetric.

## What this repository does not contain

No source documents and no data releases. The transcripts and quarterly
reports are copyrighted by their issuers and by the service that produces the
transcripts; `data/` and `exports/` are excluded from version control.

`src/stage_1_fetch.py` downloads the documents from the URLs recorded in
`src/config.py` into a local `data/raw/`. Whoever runs it is
responsible for observing the terms of the respective source: the SEC fair
access policy for EDGAR, and the terms of jpmorganchase.com for the
transcripts published there.

## Stages

| Script | Does |
|---|---|
| `stage_1_fetch.py` | Downloads the filings and records them in `filings` |
| `stage_2_parse.py` | Turns a transcript into calls and utterances |
| `stage_3_segment.py` | Splits utterances into sentences (syntok) |
| `stage_4_metrics.py` | Reads the key figures of the quarterly reports |
| `stage_final_export.py` | Writes a dated release of CSVs plus a README |

UBS files its transcripts with the SEC as Form 6-K, so they are HTML.
JPMorgan is a domestic issuer and does not file its calls at all; its official
transcripts are PDFs from the investor relations site. `parsers/jpm_transcript.py`
and `parsers/jpm_metrics.py` hold the branches that difference requires.

## Setup

PostgreSQL 18 in Docker, Python 3.12. Copy `.env.example` to `.env` and fill
it in; the SEC requires a contactable "Name email" string as the user agent.

    docker compose up -d
    alembic upgrade head
    python src/stage_1_fetch.py --bank UBS

## Licence

MIT, see LICENSE. The licence covers the code in this repository only, not the
documents it retrieves nor the data derived from them.
