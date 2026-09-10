
from __future__ import annotations

import json
import hashlib
import os
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import fitz  # pymupdf
import langextract as lx
import networkx as nx
from langextract import factory

DEFAULT_PROMPT = """
Extract entities and the relationships between them from Russia-Ukraine conflict analysis.

The source is a synthetic source packet. Each numbered statement has this shape:

    P5 | PDF-001-C03   SYNTHETIC_ASSERTION
    <the claim, in prose>
    Entities: a; b; c | Relation: THREATENS | Extraction confidence: 0.87 |
    Illustrative PHIA: B2 | Time scope: 2022-2026

Extract two classes:

  "entity" - a named thing the analysis is about. Set attributes:
      type : one of actor, force, organisation, programme, capability, materiel,
             infrastructure, industry, location, condition, metric
      alias: the short form if the text also uses one (e.g. "AFU" for
             "Armed Forces of Ukraine"), otherwise omit

  "relationship" - a link between two entities. Set attributes:
      subject, predicate, object : predicate MUST be the UPPER_SNAKE_CASE value from
             that statement's "Relation:" field. Never invent a predicate.
      claim_id   : the id from the header line, e.g. "PDF-001-C03"
      status     : ASSESSED | SYNTHETIC_ASSERTION | INFERRED_BY_RULE | GUIDANCE
      confidence : the "Extraction confidence:" number, as a string
      phia       : the "Illustrative PHIA:" code
      time_scope : the "Time scope:" value
      is_analytic_rule : "true" when status is GUIDANCE or time scope is "Analytic rule".
             These statements describe how to model the data, not facts about the war.

Rules:
  - Use the EXACT text from the source for extraction_text. Never paraphrase or normalise.
  - extraction_text for a relationship should be the prose span that states the link,
    not the annotation line.
  - Take subject/object surface strings from the "Entities:" list where possible, so
    they join up with the entity extractions.
  - Do not infer relationships the text does not state.
  - Extract in order of appearance. Do not repeat the same span twice.
  - Skip the packet boilerplate (cover page, "IMPORTANT:", "Content status", tag lists).
"""

CORPUS_PROMPT = """
Extract entities and the relationships between them from a synthetic corporate and
critical-infrastructure document corpus.

Documents are registry extracts, procurement award notices, engineering outage logs,
financial filings, legal notices, trade press, press releases, human source reports,
open-source collection and all-source assessments. Each opens with a header block
(DOCUMENT ID, TYPE, TITLE, DATE, ORIGIN, HANDLING) followed by the body.

Extract two classes:

  "entity" - a named thing the document is about. Set attributes:
      type : one of company, person, site, equipment, jurisdiction, sector, event
      alias: the short form if the text also uses one (e.g. "DO" for
             "Dniprovenko Optomerezha JSC"), otherwise omit
      A monetary value, date, percentage, duration, serial or reference number is
      NEVER an entity. Those belong on a relationship's attributes.
      Give an event a short label ("control cabinet water ingress"), not the whole
      sentence describing it.

  "relationship" - a link between two entities. Set attributes:
      subject, predicate, object
      predicate MUST be one of:
          OWNS_SHARE_IN, BENEFICIAL_OWNER_OF, OFFICER_OF, OPERATES, MAINTAINS,
          SUPPLIES, AWARDED_CONTRACT_TO, CONTRACTING_AUTHORITY_FOR, LOCATED_IN,
          HAS_ASSET, DEPENDS_ON, AFFECTED_BY, RELATED_PARTY_OF,
          LITIGATION_AGAINST, CORRELATED_WITH, ATTRIBUTED_TO, MET_WITH,
          CONCENTRATION_RISK_AT
      Never invent a predicate. If no listed predicate fits, do not extract the link.
      status : how strongly the document holds the claim -
          RECORDED - a documentary record (registry, procurement, filing, outage log)
          REPORTED - single-source reporting (human, open source, technical collection)
          ALLEGED  - pleaded or claimed but not established (legal notices, allegations)
          ASSESSED - an all-source analytical judgement
      source_grade : the grade attached to the statement where the document gives one,
             e.g. "D3", "C3", "MODERATE", "LOW". Omit when there is none.
      role       : for OFFICER_OF, the office held, e.g. "Chief Executive Officer"
      percent    : for OWNS_SHARE_IN, the disclosed holding, e.g. "60%"
      value      : the contract or transaction value, e.g. "UAH 147,000,000"
      valid_from : the date the relationship is stated to begin, e.g. "2021-10-19"

Direction matters. Always:
      <shareholder> OWNS_SHARE_IN <company held>
      <person> OFFICER_OF <company>
      <contracting authority> AWARDED_CONTRACT_TO <supplier>
      <operator or contractor> OPERATES / MAINTAINS <site>
      <site> HAS_ASSET <equipment>, <site> DEPENDS_ON <other site>
  Never reverse these, even if the sentence names the parties the other way round.

Choosing between the easily confused predicates:
  - OWNS_SHARE_IN is ONLY for disclosed equity or shareholding. A company that
    "holds a framework" or "holds a contract" covering a site MAINTAINS that site -
    holding a contract is not holding shares.
  - A contractor recorded as attending, servicing or holding a maintenance framework
    at a site MAINTAINS it. SUPPLIES is for delivering goods or components.
  - OPERATES is for the operator of record of a site; MAINTAINS is for the contractor
    that services it. They are different companies and different edges.
  - HAS_ASSET links a site only to a named item of plant, such as those listed under
    "AFFECTED PLANT". A site's own ASSET REF, COMMISSIONED year, RATED capacity and
    CRITICALITY are properties of that site - not entities, and not assets.

Rules:
  - Use the EXACT text from the source for extraction_text. Never paraphrase or normalise.
  - Do not extract from the header block or the "[SYNTHETIC CORPUS ...]" banner.
  - Every name used as a relationship subject or object must ALSO be extracted as an
    "entity" with a type, so no relationship points at an untyped node.
  - Never build a relationship out of a negative or nil statement. Lines such as
    "No officers currently filed." or "No shareholder above the 10% disclosure
    threshold." record an absence and must produce no extraction at all.
  - Do not infer relationships the text does not state. Where a document attributes a
    claim to a party, keep the claim, not your own judgement of it.
  - A site is a physical facility (substation, plant, depot, station, works).
    A company is a legal entity. A person is a named individual.
  - Extract in order of appearance. Do not repeat the same span twice.
"""


GENERAL_PROMPT = """
Extract entities and the relationships between them from a real-world report or
business/policy document (not a synthetic packet - ordinary prose, headings,
tables and bullet lists).

Extract two classes:

  "entity" - a named thing the document is about. Set attributes:
      type : one of organisation, project, person, location, infrastructure,
             technology, policy, target, document
      alias: the short form if the text also uses one (e.g. an acronym),
             otherwise omit
      A bare monetary value, date, percentage or duration is NEVER an entity.
      Those belong on a relationship's attributes instead.

  "relationship" - a link between two entities. Set attributes:
      subject, predicate, object
      predicate: a short, precise UPPER_SNAKE_CASE verb phrase that names the
             relationship THIS SENTENCE states, e.g. OPERATES, OWNS, SUPPLIES,
             LOCATED_IN, FUNDS, PART_OF, LEADS, COLLABORATES_WITH, TARGETS,
             DELIVERS, DEPENDS_ON, REPORTS_TO. There is no fixed list and no
             literal annotation field to copy this from anywhere in the source
             - read the sentence and coin the predicate yourself. Never invent
             a relationship the text does not state, but do not withhold one
             just because no field names it.
      value      : an associated figure the sentence states, e.g. "1.2 GW",
             "60%", "2030", "GBP 10bn" - omit when there is none.
      valid_time : a date or year range the relationship is scoped to, if the
             sentence gives one - omit otherwise.

Rules:
  - Use the EXACT text from the source for extraction_text. Never paraphrase.
  - Every name used as a relationship subject or object must ALSO be extracted
    as an "entity" with a type, so no relationship points at an untyped node.
  - Do not build a relationship out of a negative or purely descriptive
    statement with no named counterpart ("emissions fell to 4.5 MtCO2/yr" on
    its own has no second entity - do not force one).
  - Extract in order of appearance. Do not repeat the same span twice.
"""


def _general_examples() -> list:
    """Few-shot examples for GENERAL_PROMPT. Deliberately generic (not lifted
    from any one real report) - they exist to show the model that a predicate
    is coined from the sentence, not copied from an annotation field."""
    return [
        lx.data.ExampleData(
            text=(
                "Riverside Energy Partners operates the Northbank CCS project in "
                "Fenwick, which will capture 4 MtCO2/yr from 2028. The project is "
                "co-funded by the regional development authority."
            ),
            extractions=[
                lx.data.Extraction(
                    extraction_class="entity",
                    extraction_text="Riverside Energy Partners",
                    attributes={"type": "organisation"},
                ),
                lx.data.Extraction(
                    extraction_class="entity",
                    extraction_text="Northbank CCS project",
                    attributes={"type": "project"},
                ),
                lx.data.Extraction(
                    extraction_class="entity",
                    extraction_text="Fenwick",
                    attributes={"type": "location"},
                ),
                lx.data.Extraction(
                    extraction_class="entity",
                    extraction_text="regional development authority",
                    attributes={"type": "organisation"},
                ),
                lx.data.Extraction(
                    extraction_class="relationship",
                    extraction_text="Riverside Energy Partners operates the Northbank CCS project",
                    attributes={
                        "subject": "Riverside Energy Partners", "predicate": "OPERATES",
                        "object": "Northbank CCS project",
                    },
                ),
                lx.data.Extraction(
                    extraction_class="relationship",
                    extraction_text="Northbank CCS project in Fenwick",
                    attributes={
                        "subject": "Northbank CCS project", "predicate": "LOCATED_IN",
                        "object": "Fenwick",
                    },
                ),
                lx.data.Extraction(
                    extraction_class="relationship",
                    extraction_text="capture 4 MtCO2/yr from 2028",
                    attributes={
                        "subject": "Northbank CCS project", "predicate": "TARGETS",
                        "object": "Northbank CCS project",
                        "value": "4 MtCO2/yr", "valid_time": "2028",
                    },
                ),
                lx.data.Extraction(
                    extraction_class="relationship",
                    extraction_text="co-funded by the regional development authority",
                    attributes={
                        "subject": "regional development authority", "predicate": "FUNDS",
                        "object": "Northbank CCS project",
                    },
                ),
            ],
        ),
        lx.data.ExampleData(
            text=(
                "The plan has been developed by three organisations working in "
                "collaboration - Aventyne Group, the Coastal Process Industries "
                "Cluster, and the Mayoral Combined Authority. Aventyne's blue "
                "hydrogen plant will supply hydrogen to the fertiliser works, "
                "displacing its use of natural gas by 2030."
            ),
            extractions=[
                lx.data.Extraction(
                    extraction_class="entity",
                    extraction_text="Aventyne Group",
                    attributes={"type": "organisation"},
                ),
                lx.data.Extraction(
                    extraction_class="entity",
                    extraction_text="Coastal Process Industries Cluster",
                    attributes={"type": "organisation"},
                ),
                lx.data.Extraction(
                    extraction_class="entity",
                    extraction_text="Mayoral Combined Authority",
                    attributes={"type": "organisation"},
                ),
                lx.data.Extraction(
                    extraction_class="entity",
                    extraction_text="blue hydrogen plant",
                    attributes={"type": "infrastructure"},
                ),
                lx.data.Extraction(
                    extraction_class="entity",
                    extraction_text="fertiliser works",
                    attributes={"type": "infrastructure"},
                ),
                lx.data.Extraction(
                    extraction_class="relationship",
                    extraction_text="developed by three organisations working in collaboration - Aventyne Group, the Coastal Process Industries Cluster, and the Mayoral Combined Authority",
                    attributes={
                        "subject": "Aventyne Group", "predicate": "COLLABORATES_WITH",
                        "object": "Coastal Process Industries Cluster",
                    },
                ),
                lx.data.Extraction(
                    extraction_class="relationship",
                    extraction_text="Aventyne's blue hydrogen plant",
                    attributes={
                        "subject": "Aventyne Group", "predicate": "OWNS",
                        "object": "blue hydrogen plant",
                    },
                ),
                lx.data.Extraction(
                    extraction_class="relationship",
                    extraction_text="will supply hydrogen to the fertiliser works",
                    attributes={
                        "subject": "blue hydrogen plant", "predicate": "SUPPLIES",
                        "object": "fertiliser works",
                    },
                ),
                lx.data.Extraction(
                    extraction_class="relationship",
                    extraction_text="displacing its use of natural gas by 2030",
                    attributes={
                        "subject": "blue hydrogen plant", "predicate": "TARGETS",
                        "object": "fertiliser works",
                        "valid_time": "2030",
                    },
                ),
            ],
        ),
    ]


def _default_examples() -> list:
    return [
        # 1. Standard capability-threatens-infrastructure claim.
        lx.data.ExampleData(
            text=(
                "P5 | PDF-001-C03   SYNTHETIC_ASSERTION\n"
                "Dense observation and precision fires reduced the safety of visible command "
                "posts, logistics sites and vehicle concentrations.\n"
                "Entities: precision fires; logistics sites; command posts | "
                "Relation: THREATENS | Extraction confidence: 0.87 | "
                "Illustrative PHIA: B2 | Time scope: 2022-2026"
            ),
            extractions=[
                lx.data.Extraction(
                    extraction_class="entity",
                    extraction_text="precision fires",
                    attributes={"type": "capability"},
                ),
                lx.data.Extraction(
                    extraction_class="entity",
                    extraction_text="command posts",
                    attributes={"type": "infrastructure"},
                ),
                lx.data.Extraction(
                    extraction_class="entity",
                    extraction_text="logistics sites",
                    attributes={"type": "infrastructure"},
                ),
                lx.data.Extraction(
                    extraction_class="relationship",
                    extraction_text="reduced the safety of visible command posts",
                    attributes={
                        "subject": "precision fires", "predicate": "THREATENS",
                        "object": "command posts",
                        "claim_id": "PDF-001-C03", "status": "SYNTHETIC_ASSERTION",
                        "confidence": "0.87", "phia": "B2", "time_scope": "2022-2026",
                    },
                ),
                lx.data.Extraction(
                    extraction_class="relationship",
                    extraction_text="logistics sites and vehicle concentrations",
                    attributes={
                        "subject": "precision fires", "predicate": "THREATENS",
                        "object": "logistics sites",
                        "claim_id": "PDF-001-C03", "status": "SYNTHETIC_ASSERTION",
                        "confidence": "0.87", "phia": "B2", "time_scope": "2022-2026",
                    },
                ),
            ],
        ),
        # 2. Actor / programme / industry claim, with an alias in play.
        lx.data.ExampleData(
            text=(
                "P4 | PDF-007-C02   SYNTHETIC_ASSERTION\n"
                "Ukrainian state developed a comparatively distributed innovation ecosystem "
                "linking military users, volunteers, firms and government programmes.\n"
                "Entities: Ukrainian defence industry; Brave1; military users | "
                "Relation: CONNECTS | Extraction confidence: 0.73 | "
                "Illustrative PHIA: C3 | Time scope: 2022-2026"
            ),
            extractions=[
                lx.data.Extraction(
                    extraction_class="entity",
                    extraction_text="Ukrainian state",
                    attributes={"type": "actor"},
                ),
                lx.data.Extraction(
                    extraction_class="entity",
                    extraction_text="Brave1",
                    attributes={"type": "programme"},
                ),
                lx.data.Extraction(
                    extraction_class="entity",
                    extraction_text="military users",
                    attributes={"type": "actor"},
                ),
                lx.data.Extraction(
                    extraction_class="relationship",
                    extraction_text="linking military users, volunteers, firms and government programmes",
                    attributes={
                        "subject": "Ukrainian state", "predicate": "CONNECTS",
                        "object": "military users",
                        "claim_id": "PDF-007-C02", "status": "SYNTHETIC_ASSERTION",
                        "confidence": "0.73", "phia": "C3", "time_scope": "2022-2026",
                    },
                ),
            ],
        ),
        # 3. GUIDANCE - a modelling instruction, NOT a fact about the war. Without it the
        #    model files analytic rules as ordinary edges and quietly corrupts the graph.
        lx.data.ExampleData(
            text=(
                "P09 | PDF-007-C07   GUIDANCE\n"
                "A production-capacity estimate is not the same quantity as delivered systems, "
                "contracted output or systems available at the front.\n"
                "Entities: production capacity; deliveries; frontline availability | "
                "Relation: METRIC_WARNING | Extraction confidence: 0.95 | "
                "Illustrative PHIA: N/AN/A | Time scope: Analytic rule"
            ),
            extractions=[
                lx.data.Extraction(
                    extraction_class="entity",
                    extraction_text="production capacity",
                    attributes={"type": "metric"},
                ),
                lx.data.Extraction(
                    extraction_class="entity",
                    extraction_text="deliveries",
                    attributes={"type": "metric"},
                ),
                lx.data.Extraction(
                    extraction_class="entity",
                    extraction_text="frontline availability",
                    attributes={"type": "metric"},
                ),
                lx.data.Extraction(
                    extraction_class="relationship",
                    extraction_text="is not the same quantity as delivered systems",
                    attributes={
                        "subject": "production capacity", "predicate": "METRIC_WARNING",
                        "object": "deliveries",
                        "claim_id": "PDF-007-C07", "status": "GUIDANCE",
                        "confidence": "0.95", "phia": "N/A", "time_scope": "Analytic rule",
                        "is_analytic_rule": "true",
                    },
                ),
            ],
        ),
    ]


def _corpus_examples() -> list:
    """Few-shot examples for Data/corpus. Spans are lifted verbatim from real documents
    so they align, and cover the four largest classes (registry 63, outage log 61,
    press 43, procurement 28) plus graded single-source reporting."""
    return [
        # 1. Registry extract - officers, shareholding, jurisdiction. Also the nil-return
        #    trap: "No shareholder above..." must yield nothing.
        lx.data.ExampleData(
            text=(
                "REGISTERED NAME     : Buhrichka Enerhomerezha PrJSC\n"
                "JURISDICTION        : Ukraine\n"
                "PRINCIPAL ACTIVITY  : grid\n"
                "STATUS              : active\n"
                "\n"
                "OFFICERS OF RECORD\n"
                "  - Zoriana Didenko — Chief Executive Officer, appointed 2021-10-19\n"
                "  - Ruslan Hrytsenko — Finance Director, appointed 2022-07-14\n"
                "\n"
                "SHAREHOLDINGS DISCLOSED\n"
                "  - Verkhovyna Industrial Group PJSC holds 60% (from 2019-07-10)"
            ),
            extractions=[
                lx.data.Extraction(
                    extraction_class="entity",
                    extraction_text="Buhrichka Enerhomerezha PrJSC",
                    attributes={"type": "company"},
                ),
                lx.data.Extraction(
                    extraction_class="entity",
                    extraction_text="Ukraine",
                    attributes={"type": "jurisdiction"},
                ),
                lx.data.Extraction(
                    extraction_class="entity",
                    extraction_text="Zoriana Didenko",
                    attributes={"type": "person"},
                ),
                lx.data.Extraction(
                    extraction_class="entity",
                    extraction_text="Ruslan Hrytsenko",
                    attributes={"type": "person"},
                ),
                lx.data.Extraction(
                    extraction_class="entity",
                    extraction_text="Verkhovyna Industrial Group PJSC",
                    attributes={"type": "company"},
                ),
                lx.data.Extraction(
                    extraction_class="relationship",
                    extraction_text="Zoriana Didenko — Chief Executive Officer, appointed 2021-10-19",
                    attributes={
                        "subject": "Zoriana Didenko", "predicate": "OFFICER_OF",
                        "object": "Buhrichka Enerhomerezha PrJSC",
                        "status": "RECORDED", "role": "Chief Executive Officer",
                        "valid_from": "2021-10-19",
                    },
                ),
                lx.data.Extraction(
                    extraction_class="relationship",
                    extraction_text="Ruslan Hrytsenko — Finance Director, appointed 2022-07-14",
                    attributes={
                        "subject": "Ruslan Hrytsenko", "predicate": "OFFICER_OF",
                        "object": "Buhrichka Enerhomerezha PrJSC",
                        "status": "RECORDED", "role": "Finance Director",
                        "valid_from": "2022-07-14",
                    },
                ),
                lx.data.Extraction(
                    extraction_class="relationship",
                    extraction_text="Verkhovyna Industrial Group PJSC holds 60% (from 2019-07-10)",
                    attributes={
                        "subject": "Verkhovyna Industrial Group PJSC",
                        "predicate": "OWNS_SHARE_IN",
                        "object": "Buhrichka Enerhomerezha PrJSC",
                        "status": "RECORDED", "percent": "60%",
                        "valid_from": "2019-07-10",
                    },
                ),
            ],
        ),
        # 2. Procurement award. The NOTES line is a negative finding and is deliberately
        #    left unextracted.
        lx.data.ExampleData(
            text=(
                "CONTRACTING AUTHORITY : Pivdenhrebl Hrid Systems LLC\n"
                "AWARDED SUPPLIER      : Karpatvuzol Prombudmontazh SE\n"
                "SUBJECT               : maintenance and repair services\n"
                "VALUE                 : UAH 147,000,000\n"
                "SITE                  : Terenivske Transformer Depot\n"
                "\n"
                "NOTES\n"
                "  - Justification recorded as technical exclusivity. No alternative "
                "supplier was assessed as capable of delivering within the required timeframe.\n"
                "  - The supplier holds concurrent frameworks covering: Solomiyivske "
                "Pumping Station."
            ),
            extractions=[
                lx.data.Extraction(
                    extraction_class="entity",
                    extraction_text="Pivdenhrebl Hrid Systems LLC",
                    attributes={"type": "company"},
                ),
                lx.data.Extraction(
                    extraction_class="entity",
                    extraction_text="Karpatvuzol Prombudmontazh SE",
                    attributes={"type": "company"},
                ),
                lx.data.Extraction(
                    extraction_class="entity",
                    extraction_text="Terenivske Transformer Depot",
                    attributes={"type": "site"},
                ),
                lx.data.Extraction(
                    extraction_class="entity",
                    extraction_text="Solomiyivske Pumping Station",
                    attributes={"type": "site"},
                ),
                lx.data.Extraction(
                    extraction_class="relationship",
                    extraction_text="AWARDED SUPPLIER      : Karpatvuzol Prombudmontazh SE",
                    attributes={
                        "subject": "Pivdenhrebl Hrid Systems LLC",
                        "predicate": "AWARDED_CONTRACT_TO",
                        "object": "Karpatvuzol Prombudmontazh SE",
                        "status": "RECORDED", "value": "UAH 147,000,000",
                    },
                ),
                lx.data.Extraction(
                    extraction_class="relationship",
                    extraction_text="SITE                  : Terenivske Transformer Depot",
                    attributes={
                        "subject": "Karpatvuzol Prombudmontazh SE",
                        "predicate": "MAINTAINS",
                        "object": "Terenivske Transformer Depot",
                        "status": "RECORDED",
                    },
                ),
                # "holds ... frameworks covering" is maintenance, not shareholding.
                lx.data.Extraction(
                    extraction_class="relationship",
                    extraction_text="The supplier holds concurrent frameworks covering: Solomiyivske Pumping Station.",
                    attributes={
                        "subject": "Karpatvuzol Prombudmontazh SE",
                        "predicate": "MAINTAINS",
                        "object": "Solomiyivske Pumping Station",
                        "status": "RECORDED",
                    },
                ),
            ],
        ),
        # 3. Outage log - operator, plant asset and the site-to-site dependency that makes
        #    cascade analysis possible.
        lx.data.ExampleData(
            text=(
                "SITE          : Substation 'Lypove' (330/110 kV substation)\n"
                "OPERATOR      : Pivdenhrebl Hrid Systems LLC\n"
                "ASSET REF     : Lypove SS\n"
                "COMMISSIONED  : 1987\n"
                "RATED         : 40 MW\n"
                "CRITICALITY   : MEDIUM\n"
                "\n"
                "EVENT: control cabinet water ingress recorded at 07:26. Duration 704 "
                "minutes. Customer supply interrupted.\n"
                "\n"
                "AFFECTED PLANT:\n"
                "  - GT-Aurelia 40MW (gas turbine), serial GT27879\n"
                "\n"
                "ATTENDANCE: Karpatvuzol Turbomash PrJSC attended under the standing "
                "maintenance framework.\n"
                "\n"
                "DEPENDENCY NOTE: this site takes supply from Substation 'Mlynivske'; "
                "a fault at that location would propagate here."
            ),
            extractions=[
                lx.data.Extraction(
                    extraction_class="entity",
                    extraction_text="Substation 'Lypove'",
                    attributes={"type": "site"},
                ),
                lx.data.Extraction(
                    extraction_class="entity",
                    extraction_text="Pivdenhrebl Hrid Systems LLC",
                    attributes={"type": "company"},
                ),
                lx.data.Extraction(
                    extraction_class="entity",
                    extraction_text="GT-Aurelia 40MW",
                    attributes={"type": "equipment"},
                ),
                lx.data.Extraction(
                    extraction_class="entity",
                    extraction_text="Substation 'Mlynivske'",
                    attributes={"type": "site"},
                ),
                lx.data.Extraction(
                    extraction_class="entity",
                    extraction_text="Karpatvuzol Turbomash PrJSC",
                    attributes={"type": "company"},
                ),
                # Short label for the event, not the whole EVENT sentence.
                lx.data.Extraction(
                    extraction_class="entity",
                    extraction_text="control cabinet water ingress",
                    attributes={"type": "event"},
                ),
                lx.data.Extraction(
                    extraction_class="relationship",
                    extraction_text="OPERATOR      : Pivdenhrebl Hrid Systems LLC",
                    attributes={
                        "subject": "Pivdenhrebl Hrid Systems LLC", "predicate": "OPERATES",
                        "object": "Substation 'Lypove'", "status": "RECORDED",
                    },
                ),
                lx.data.Extraction(
                    extraction_class="relationship",
                    extraction_text="GT-Aurelia 40MW (gas turbine), serial GT27879",
                    attributes={
                        "subject": "Substation 'Lypove'", "predicate": "HAS_ASSET",
                        "object": "GT-Aurelia 40MW", "status": "RECORDED",
                    },
                ),
                lx.data.Extraction(
                    extraction_class="relationship",
                    extraction_text="Karpatvuzol Turbomash PrJSC attended under the standing maintenance framework.",
                    attributes={
                        "subject": "Karpatvuzol Turbomash PrJSC", "predicate": "MAINTAINS",
                        "object": "Substation 'Lypove'", "status": "RECORDED",
                    },
                ),
                lx.data.Extraction(
                    extraction_class="relationship",
                    extraction_text="control cabinet water ingress recorded at 07:26",
                    attributes={
                        "subject": "Substation 'Lypove'", "predicate": "AFFECTED_BY",
                        "object": "control cabinet water ingress", "status": "RECORDED",
                    },
                ),
                lx.data.Extraction(
                    extraction_class="relationship",
                    extraction_text="this site takes supply from Substation 'Mlynivske'",
                    attributes={
                        "subject": "Substation 'Lypove'", "predicate": "DEPENDS_ON",
                        "object": "Substation 'Mlynivske'", "status": "RECORDED",
                    },
                ),
            ],
        ),
        # 4. Human source report. Paragraph grades differ within one document, so the
        #    grade travels on the edge, not the document.
        lx.data.ExampleData(
            text=(
                "2. (D3) Source has met Volodymyr Shevchuk on two occasions. Shevchuk is "
                "described as holding decision authority over procurement.\n"
                "\n"
                "3. (C3) Source indicated that beneficial control of Shchedryn "
                "Kompresorservis rests with interests represented by Bilokamin Industrial "
                "Group PrJSC, though source could not produce documentation."
            ),
            extractions=[
                lx.data.Extraction(
                    extraction_class="entity",
                    extraction_text="Volodymyr Shevchuk",
                    attributes={"type": "person"},
                ),
                lx.data.Extraction(
                    extraction_class="entity",
                    extraction_text="Shchedryn Kompresorservis",
                    attributes={"type": "company"},
                ),
                lx.data.Extraction(
                    extraction_class="entity",
                    extraction_text="Bilokamin Industrial Group PrJSC",
                    attributes={"type": "company"},
                ),
                lx.data.Extraction(
                    extraction_class="relationship",
                    extraction_text="beneficial control of Shchedryn Kompresorservis rests with interests represented by Bilokamin Industrial Group PrJSC",
                    attributes={
                        "subject": "Bilokamin Industrial Group PrJSC",
                        "predicate": "BENEFICIAL_OWNER_OF",
                        "object": "Shchedryn Kompresorservis",
                        "status": "REPORTED", "source_grade": "C3",
                    },
                ),
            ],
        ),
    ]


# Kept in step with the predicate list in CORPUS_PROMPT. The prompt asks; this
# enforces. A 4B model will occasionally put a status ("ASSESSED") or a paraphrase in
# the predicate slot, and one stray predicate is a permanently unqueryable edge.
CORPUS_PREDICATES = frozenset({
    "OWNS_SHARE_IN", "BENEFICIAL_OWNER_OF", "OFFICER_OF", "OPERATES", "MAINTAINS",
    "SUPPLIES", "AWARDED_CONTRACT_TO", "CONTRACTING_AUTHORITY_FOR", "LOCATED_IN",
    "HAS_ASSET", "DEPENDS_ON", "AFFECTED_BY", "RELATED_PARTY_OF",
    "LITIGATION_AGAINST", "CORRELATED_WITH", "ATTRIBUTED_TO", "MET_WITH",
    "CONCENTRATION_RISK_AT",
})

# A bare quantity or date is never an entity in either corpus, but the model keeps
# offering "UAH 3147 million" and "1987" as nodes. Anchored so that legitimate names
# carrying numbers ("GT-Aurelia 40MW", "Substation 'Lypove'") are untouched.
_QUANTITY = re.compile(
    r"(?:UAH|USD|EUR|GBP)?\s*\d[\d\s.,/-]*\s*"
    r"(?:%|million|billion|thousand|MW|kV|minutes|hours|days|years)?",
    re.IGNORECASE,
)


def is_quantity(label: str) -> bool:
    s = label.strip()
    return bool(s) and _QUANTITY.fullmatch(s) is not None


@dataclass
class ExtractionProfile:
    """A corpus-specific prompt and example set.

    The two corpora in this project are unrelated domains - the PDF packets are
    Russia-Ukraine conflict analysis, Data/corpus is a fictional corporate and
    infrastructure network - so they need different prompts, closed predicate
    vocabularies and entity types. Running one prompt over the other corpus produces
    confident nonsense, so the profile is chosen per document, not per run.
    """

    name: str
    prompt: str
    examples: list
    signature: tuple[str, ...] = ()
    # Closed predicate vocabulary. Empty means open - the packet corpus supplies its
    # own predicate in each statement's "Relation:" field, so it cannot be listed here.
    predicates: frozenset[str] = frozenset()

    def matches(self, text: str) -> bool:
        head = text[:4000]
        return any(s in head for s in self.signature)


PROFILE_NAMES = ("packet", "corpus", "general")
_PROFILES: dict[str, ExtractionProfile] = {}


def get_profile(name: str) -> ExtractionProfile:
    if name not in _PROFILES:
        if name == "packet":
            _PROFILES[name] = ExtractionProfile(
                name="packet",
                prompt=DEFAULT_PROMPT,
                examples=_default_examples(),
                signature=("SYNTHETIC SOURCE PACKET", "Illustrative PHIA"),
            )
        elif name == "corpus":
            _PROFILES[name] = ExtractionProfile(
                name="corpus",
                prompt=CORPUS_PROMPT,
                examples=_corpus_examples(),
                signature=("[SYNTHETIC CORPUS", "SYNTHETIC//TRAINING-CORPUS"),
                predicates=CORPUS_PREDICATES,
            )
        elif name == "general":
            # No signature: this profile is never matched by detect_profile's
            # loop, only reached via its fallback branch or an explicit
            # `profile="general"` selection. Its prompt does not tie predicates
            # to a literal per-statement annotation field the way "packet"
            # does, so it is safe to use on ordinary prose documents that carry
            # no such field - see the fallback comment below for why that
            # distinction matters.
            _PROFILES[name] = ExtractionProfile(
                name="general",
                prompt=GENERAL_PROMPT,
                examples=_general_examples(),
            )
        else:
            raise ValueError(f"unknown profile {name!r}, expected one of {PROFILE_NAMES}")
    return _PROFILES[name]


def detect_profile(text: str) -> ExtractionProfile:
    for name in PROFILE_NAMES:
        profile = get_profile(name)
        if profile.matches(text):
            return profile
    # Neither synthetic corpus's signature matched: this is a real, unmarked
    # document. "packet"'s prompt ties every predicate to copying a literal
    # "Relation: X" annotation field that only exists in its synthetic input
    # format: on real prose a small model asked to "never invent a predicate"
    # reliably drafts zero relationships rather than bend that rule, which is
    # exactly why entity counts and relationship counts on a real document can
    # come out wildly lopsided. "general" carries no such dependency, so it is
    # the correct default for the common case - a real document nobody has
    # written a dedicated profile for yet.
    return get_profile("general")


# Header block of a Data/corpus document. These are per-document constants sitting in a
# fixed-delimiter block, so a regex reads them exactly - no reason to spend a 4B model's
# attention on copying an id it can drop or hallucinate.
_HEADER_FIELD = re.compile(r"^([A-Z][A-Z /'-]{2,21}?)\s*:\s*(.+?)\s*$", re.MULTILINE)
_DOC_META_FIELDS = {
    "DOCUMENT ID": "doc_id",
    "TYPE": "doc_type",
    "TITLE": "doc_title",
    "DATE": "doc_date",
    "ORIGIN": "doc_origin",
}


def document_metadata(text: str) -> dict:
    """Pull the header block. Bounded to the first 1200 chars so body fields such as
    "OPERATOR      : ..." are not mistaken for document metadata."""
    meta: dict[str, str] = {}
    for m in _HEADER_FIELD.finditer(text[:1200]):
        key = _DOC_META_FIELDS.get(m.group(1).strip())
        if key and key not in meta:
            meta[key] = m.group(2).strip()
    return meta


TYPE_COLOURS = {
    "actor":          "#ff4b4b",
    "force":          "#ff7043",
    "organisation":   "#ffb84b",
    "programme":      "#d4a017",
    "capability":     "#4b9bff",
    "materiel":       "#5c6bc0",
    "infrastructure": "#4bff9b",
    "industry":       "#26a69a",
    "location":       "#b84bff",
    "condition":      "#8d6e63",
    "metric":         "#78909c",
    # Data/corpus types.
    "company":        "#29b6f6",
    "person":         "#ec407a",
    "site":           "#66bb6a",
    "equipment":      "#7e57c2",
    "jurisdiction":   "#ab47bc",
    "sector":         "#c0ca33",
    "document":       "#a1887f",
    "event":          "#ffa726",
    # General-profile types not already covered above.
    "project":        "#00acc1",
    "technology":     "#3f51b5",
    "policy":         "#6d4c41",
    "target":         "#fdd835",
    "unknown":        "#888888",
}


PDF_SUFFIXES = {".pdf"}
TEXT_SUFFIXES = {".txt", ".text", ".md"}
DEFAULT_PATTERNS = ("*.pdf", "*.txt")


def as_text(value) -> str:
    """Coerce a model-extracted field to a string.

    Extraction fields are contractually strings, but small local models
    occasionally emit a list (e.g. multiple candidate spans) where a single
    string was expected; join those rather than letting them crash normalise().
    """
    if isinstance(value, (list, tuple)):
        return " ".join(as_text(v) for v in value if v not in (None, ""))
    return "" if value is None else str(value)


def normalise(s: str) -> str:
    return re.sub(r"\s+", " ", as_text(s)).strip().lower()


# Words of surrounding source text kept on each side of an extracted span, for
# the review drawer. A bare entity string ("Ukraine") tells a reviewer nothing
# about whether the extraction is right; a paragraph's worth of the prose it
# came from does. Counted in words rather than characters (the previous
# design: 50 chars per side, ~16-18 words total) so an analyst gets a
# consistent amount of reading context - about 50 words - regardless of a
# document's average word length.
EXCERPT_CONTEXT_WORDS = 25


def excerpt_for(
    source: str,
    start: int | None,
    end: int | None,
    context_words: int = EXCERPT_CONTEXT_WORDS,
) -> str:
    """The extracted span plus ~context_words words on each side (~50 words of
    total context at the default), so the review drawer shows enough
    surrounding prose to judge the extraction without opening the source
    document."""
    if start is None or end is None or not source:
        return ""
    left_words = source[:start].split()
    right_words = source[end:].split()
    left = " ".join(left_words[-context_words:])
    right = " ".join(right_words[:context_words])
    span = " ".join(source[start:end].split())
    text = " ".join(part for part in (left, span, right) if part)
    prefix = "…" if len(left_words) > context_words else ""
    suffix = "…" if len(right_words) > context_words else ""
    return f"{prefix}{text}{suffix}"


@dataclass
class Triple:
    subject: str
    predicate: str
    object: str
    source_text: str = ""
    attributes: dict = field(default_factory=dict)

    def key(self):
        if self.attributes.get("implicit"):
            return (normalise(self.subject), self.predicate, normalise(self.object))
        return (
            normalise(self.subject),
            self.predicate,
            normalise(self.object),
            str(self.attributes.get("claim_id", "")),
            normalise(self.source_text),
        )


class PDFToKnowledgeGraph:
    def __init__(
        self,
        model_id: str = "google/gemma-4-e4b",
        base_url: str = "http://127.0.0.1:1234/v1",
        api_key: str = "lm-studio",
        prompt: str | None = None,
        examples: list | None = None,
        *,
        profile: str = "auto",
        max_char_buffer: int = 1500,
        extraction_passes: int = 2,
        max_workers: int = 4,
        temperature: float = 0.0,
        page_markers: bool = True,
        drop_analytic_rules: bool = False,
        verbose: bool = True,
    ):
        self.model_id = model_id
        self.base_url = base_url
        self.api_key = api_key

        # An explicit prompt or example set pins extraction to that, whatever the
        # document looks like. Otherwise "auto" picks a profile per document and a
        # named profile forces one.
        if profile != "auto" and profile not in PROFILE_NAMES:
            raise ValueError(f"unknown profile {profile!r}, expected 'auto' or {PROFILE_NAMES}")
        self.profile_name = profile
        self._override: ExtractionProfile | None = None
        if prompt is not None or examples is not None:
            base = get_profile(profile if profile != "auto" else "packet")
            self._override = ExtractionProfile(
                name="custom",
                prompt=prompt if prompt is not None else base.prompt,
                examples=examples if examples is not None else base.examples,
            )
        # Retained for callers that read these directly.
        self.prompt = self._override.prompt if self._override else prompt
        self.examples = self._override.examples if self._override else examples

        self.max_char_buffer = max_char_buffer
        self.extraction_passes = extraction_passes
        self.max_workers = max_workers
        self.temperature = temperature
        self.page_markers = page_markers
        self.drop_analytic_rules = drop_analytic_rules
        self.verbose = verbose

        # Schema constraints (Gemini's responseSchema) aren't exposed by LM Studio's
        # OpenAI-compatible endpoint, so we parse fenced JSON instead. Malformed chunks get
        # skipped rather than crashing the run.
        self.use_schema_constraints = False
        self.model_config = factory.ModelConfig(
            model_id=model_id,
            provider="OpenAILanguageModel",   # without this, "gemma*" routes to local Ollama
            provider_kwargs={
                "base_url": base_url,
                "api_key": api_key,
                "response_format": {"type": "text"},
            },
        )
    def _log(self, *args):
        if self.verbose:
            print(*args)

    def pdf_to_text(self, path: str | Path) -> str:
        parts = []
        with fitz.open(path) as doc:
            for i, page in enumerate(doc, start=1):
                text = page.get_text("text").strip()
                if not text:
                    continue
                parts.append(f"[[page {i}]]\n{text}" if self.page_markers else text)
        return "\n\n".join(parts)

    @staticmethod
    def txt_to_text(path: str | Path) -> str:
        # errors="replace" so one bad byte in a 300-file corpus does not stop the run.
        return Path(path).read_text(encoding="utf-8", errors="replace")

    def read_document(self, path: str | Path) -> str:
        path = Path(path)
        suffix = path.suffix.lower()
        if suffix in PDF_SUFFIXES:
            return self.pdf_to_text(path)
        if suffix in TEXT_SUFFIXES:
            return self.txt_to_text(path)
        raise ValueError(f"unsupported file type {path.suffix!r}: {path}")

    def profile_for(self, text: str) -> ExtractionProfile:
        if self._override:
            return self._override
        if self.profile_name != "auto":
            return get_profile(self.profile_name)
        return detect_profile(text)

    def extract(self, text: str, profile: ExtractionProfile | None = None):
        profile = profile or self.profile_for(text)
        return lx.extract(
            text_or_documents=text,
            prompt_description=profile.prompt,
            examples=profile.examples,
            config=self.model_config,
            use_schema_constraints=self.use_schema_constraints,
            fence_output=not self.use_schema_constraints,
            max_char_buffer=self.max_char_buffer,
            extraction_passes=self.extraction_passes,
            max_workers=self.max_workers,
            temperature=self.temperature,
            debug=False,
        )

    @staticmethod
    def extractions_to_triples(annotated_doc) -> list[Triple]:
        triples: list[Triple] = []
        labels: dict[str, str] = {}
        types: dict[str, str] = {}
        # First-seen character span per entity, so an instance_of triple can
        # carry the offsets its own excerpt should be windowed around.
        spans: dict[str, tuple[int | None, int | None]] = {}

        for e in annotated_doc.extractions:
            attrs = dict(e.attributes or {})
            extraction_text = as_text(e.extraction_text)
            interval = getattr(e, "char_interval", None)
            char_start = getattr(interval, "start_pos", None)
            char_end = getattr(interval, "end_pos", None)
            if e.extraction_class == "relationship":
                subj, pred, obj = as_text(attrs.get("subject")), attrs.get("predicate"), as_text(attrs.get("object"))
                if not (subj and pred and obj):
                    continue  # partial relationship - drop it
                pred = re.sub(r"[^A-Za-z0-9]+", "_", str(pred)).strip("_").upper()
                if not pred:
                    continue
                attrs["char_start"] = char_start
                attrs["char_end"] = char_end
                triples.append(Triple(subj, pred, obj, extraction_text, attrs))
            else:
                k = normalise(extraction_text)
                labels.setdefault(k, extraction_text)
                spans.setdefault(k, (char_start, char_end))
                # First known type wins. Overwriting unconditionally let a later
                # mention with no "type" attribute downgrade an already-typed entity
                # back to unknown - likely with extraction_passes > 1.
                if types.get(k, "unknown") == "unknown":
                    candidate_type = normalise(attrs.get("type") or "")
                    types[k] = candidate_type if candidate_type in TYPE_COLOURS else "unknown"

        for k, label in labels.items():
            start, end = spans.get(k, (None, None))
            triples.append(Triple(
                label, "instance_of", types[k],
                attributes={"implicit": True, "char_start": start, "char_end": end},
            ))

        seen, unique = set(), []
        for t in triples:
            if t.key() not in seen:
                seen.add(t.key())
                unique.append(t)
        return unique


    def filter_triples(
        self, triples: list[Triple], profile: ExtractionProfile
    ) -> list[Triple]:
        """Drop what the prompt already forbids but the model produced anyway:
        off-vocabulary predicates and entities that are really quantities."""
        kept: list[Triple] = []
        bad_predicate: Counter = Counter()
        quantities: Counter = Counter()

        for t in triples:
            if t.predicate == "instance_of":
                if is_quantity(t.subject):
                    quantities[t.subject] += 1
                    continue
                kept.append(t)
                continue
            if profile.predicates and t.predicate not in profile.predicates:
                bad_predicate[t.predicate] += 1
                continue
            if is_quantity(t.subject) or is_quantity(t.object):
                quantities[t.subject if is_quantity(t.subject) else t.object] += 1
                continue
            kept.append(t)

        if bad_predicate:
            self._log(f"  dropped {sum(bad_predicate.values())} edge(s), predicate not in "
                      f"{profile.name} vocabulary: {dict(bad_predicate)}")
        if quantities:
            self._log(f"  dropped {sum(quantities.values())} quantity node(s)/edge(s): "
                      f"{list(quantities)[:5]}")
        return kept

    def triples_to_graph(
        self,
        triples: list[Triple],
        source: str = "",
        doc_meta: dict | None = None,
        document_text: str = "",
    ) -> nx.MultiDiGraph:
        """Build a directed graph from triples. Node/edge attributes match the existing
        `knowledge_graph.json` node-link format so the output drops into the current
        visualisation code unchanged."""
        # A MultiDiGraph preserves distinct predicates and evidence records between
        # the same pair of entities. A DiGraph silently overwrote earlier edges.
        G = nx.MultiDiGraph()
        doc_meta = doc_meta or {}
        node_types = {
            normalise(t.subject): t.object for t in triples if t.predicate == "instance_of"
        }
        # An entity's own extraction span, keyed the same order-independent way
        # node_types is - triples are not processed in a fixed order, and a
        # relationship's (wider, less specific) span must never win over it just
        # because that relationship happened to be processed first.
        node_spans = {
            normalise(t.subject): (t.attributes.get("char_start"), t.attributes.get("char_end"))
            for t in triples if t.predicate == "instance_of"
        }

        def add_node(label: str, char_start: int | None = None, char_end: int | None = None):
            key = normalise(label)
            if key in G:
                return key
            ntype = node_types.get(key, "unknown")
            own_start, own_end = node_spans.get(key, (None, None))
            if own_start is None:
                own_start, own_end = char_start, char_end
            excerpt = excerpt_for(document_text, own_start, own_end)
            G.add_node(
                key,
                label=label,
                title=f"{ntype.title()}: {label}",
                type=ntype,
                color=TYPE_COLOURS.get(ntype, TYPE_COLOURS["unknown"]),
                size=30,
                font={"size": 20, "color": "white", "face": "arial"},
                sources=[source] if source else [],
                # Edges already carried source_document; without it on the node
                # too, a reviewed entity showed no document at all.
                **({"source_document": source} if source else {}),
                # The review UI's evidence field prefers source_excerpt over the
                # node title, which is only a type label ("Company: Acme Ltd").
                **({"source_excerpt": excerpt} if excerpt else {}),
            )
            return key

        for t in triples:
            if t.predicate == "instance_of":
                # node_spans above already covers this triple's own span.
                add_node(t.subject)
                continue

            is_rule = (
                str(t.attributes.get("is_analytic_rule", "")).lower() == "true"
                or str(t.attributes.get("status", "")).upper() == "GUIDANCE"
                or str(t.attributes.get("time_scope", "")).casefold() == "analytic rule"
            )
            if is_rule and self.drop_analytic_rules:
                continue

            # A subject/object that never got its own "entity" extraction (the
            # model only named it inside this relationship) still deserves an
            # excerpt, so it falls back to this relationship's own span.
            rel_char_start = t.attributes.get("char_start")
            rel_char_end = t.attributes.get("char_end")
            s = add_node(t.subject, rel_char_start, rel_char_end)
            o = add_node(t.object, rel_char_start, rel_char_end)
            # Document-level facts come from the parsed header and only fill gaps the
            # extraction left; anything the model actually stated wins.
            a = {k: v for k, v in t.attributes.items() if v not in (None, "")}
            for key, value in doc_meta.items():
                a.setdefault(key, value)
            if doc_meta.get("doc_id"):
                a.setdefault("claim_id", doc_meta["doc_id"])
            if doc_meta.get("doc_date"):
                a.setdefault("time_scope", doc_meta["doc_date"])
            assertion_material = json.dumps(
                [source, a.get("claim_id"), s, t.predicate, o, t.source_text],
                ensure_ascii=False,
                separators=(",", ":"),
            )
            assertion_id = "assertion-" + hashlib.sha256(
                assertion_material.encode("utf-8")
            ).hexdigest()[:20]
            prov = " | ".join(filter(None, [
                a.get("claim_id"),
                a.get("status"),
                f"conf {a['confidence']}" if a.get("confidence") else None,
                f"PHIA {a['phia']}" if a.get("phia") else None,
                f"grade {a['source_grade']}" if a.get("source_grade") else None,
                a.get("time_scope"),
            ]))
            edge_excerpt = excerpt_for(document_text, rel_char_start, rel_char_end)
            G.add_edge(
                s, o,
                label=t.predicate,
                relation=t.predicate,
                title=f"{t.source_text}\n{prov}" if prov else t.source_text,
                source_text=t.source_text,
                # source_excerpt widens source_text with surrounding context and
                # is what the review UI actually displays as evidence; source_text
                # stays exact for the assertion hash and the title tooltip.
                **({"source_excerpt": edge_excerpt} if edge_excerpt else {}),
                source_document=source,
                assertion_id=assertion_id,
                dashes=is_rule,
                **{
                    k: v
                    for k, v in a.items()
                    if k
                    not in {
                        "subject", "predicate", "object", "label", "relation",
                        "title", "source_text", "source_document", "assertion_id",
                        "dashes",
                    }
                },
            )
        return G

    def process_document(self, path: str | Path, source_name: str = "") -> nx.MultiDiGraph:
        path = Path(path)
        # Uploads are stored under a job-prefixed filename; source_name carries
        # the name the analyst actually supplied into the graph's provenance.
        source = source_name or path.name
        text = self.read_document(path)
        if not text.strip():
            self._log(f"skip {source}: no extractable text (scanned?)")
            empty = nx.MultiDiGraph()
            empty.graph["extraction_profile"] = {"name": "", "note": "no extractable text"}
            return empty
        profile = self.profile_for(text)
        result = self.extract(text, profile)
        raw_triples = self.extractions_to_triples(result)
        triples = self.filter_triples(raw_triples, profile)
        g = self.triples_to_graph(triples, source=source, doc_meta=document_metadata(text), document_text=text)
        raw_relationships = sum(1 for t in raw_triples if t.predicate != "instance_of")
        kept_relationships = sum(1 for t in triples if t.predicate != "instance_of")
        # Carried as graph-level metadata (nx.node_link_data serialises `.graph`
        # verbatim under the "graph" key) so a caller several layers away - the
        # job runner, then the review UI - can show which profile actually ran
        # without process_document's return type changing. detect_profile()
        # silently choosing the wrong profile for a real document is exactly
        # what produced a lopsided entity/relationship count before "general"
        # existed; this is the visibility that would have caught it sooner.
        g.graph["extraction_profile"] = {
            "name": profile.name,
            "auto_detected": self._override is None and self.profile_name == "auto",
            "entities": g.number_of_nodes(),
            "relationships_extracted": raw_relationships,
            "relationships_kept": kept_relationships,
            "relationships_dropped": raw_relationships - kept_relationships,
        }
        self._log(f"{source} [{profile.name}]: "
                  f"{g.number_of_nodes()} nodes, {g.number_of_edges()} edges")
        return g

    # kg_backend/jobs.py calls process_pdf on uploads; it now handles .txt too.
    process_pdf = process_document


    def build(
        self,
        paths,
        *,
        batch_size: int | None = None,
        checkpoint_dir: str | Path | None = None,
        resume: bool = False,
    ) -> nx.MultiDiGraph:

        paths = [Path(p) for p in paths]
        merged = nx.MultiDiGraph()
        checkpoint_dir = Path(checkpoint_dir) if checkpoint_dir else None
        checkpoint_path = None
        if checkpoint_dir:
            checkpoint_dir.mkdir(parents=True, exist_ok=True)
            checkpoint_path = checkpoint_dir / "kg_checkpoint.json"


        done: set[str] = set()
        if resume and checkpoint_path and checkpoint_path.exists():
            merged = self.load(checkpoint_path)
            done = {
                s for _, d in merged.nodes(data=True) for s in (d.get("sources") or [])
            }
            self._log(f"resumed {checkpoint_path}: {merged.number_of_nodes()} nodes, "
                      f"{merged.number_of_edges()} edges, {len(done)} document(s) done")

        failures: list[tuple[str, str]] = []
        # Without a batch size the whole accumulated graph was re-serialised after every
        # single document, which is quadratic over a corpus of a few hundred.
        step = batch_size or (10 if checkpoint_path else 1)
        for start in range(0, len(paths), step):
            batch = paths[start:start + step]
            if step > 1:
                self._log(f"\n--- batch {start // step + 1}: "
                          f"documents {start + 1}-{start + len(batch)} of {len(paths)} ---")
            for p in batch:
                if p.name in done:
                    self._log(f"skip {p.name}: already in checkpoint")
                    continue
                try:
                    graph = self.process_document(p)
                except Exception as exc:   # one bad document must not lose the corpus
                    failures.append((p.name, repr(exc)))
                    self._log(f"FAILED {p.name}: {exc!r}")
                    continue
                for node_id, attributes in graph.nodes(data=True):
                    if node_id in merged:
                        current = merged.nodes[node_id]
                        sources = list(
                            dict.fromkeys(
                                [*(current.get("sources") or []), *(attributes.get("sources") or [])]
                            )
                        )
                        if current.get("type") == "unknown" and attributes.get("type") != "unknown":
                            current.update(attributes)
                        current["sources"] = sources
                    else:
                        merged.add_node(node_id, **attributes)
                for source, target, attributes in graph.edges(data=True):
                    merged.add_edge(source, target, **attributes)
            if checkpoint_path:
                self.save(merged, checkpoint_path)
                self._log(f"  checkpoint: {merged.number_of_nodes()} nodes, "
                          f"{merged.number_of_edges()} edges -> {checkpoint_path}")

        self._log(f"\ntotal: {merged.number_of_nodes()} nodes, {merged.number_of_edges()} edges")
        if failures:
            self._log(f"{len(failures)} document(s) failed:")
            for name, error in failures:
                self._log(f"  {name}: {error}")
        self._summarise(merged)
        return merged

    def build_from_dir(
        self,
        data_dir: str | Path,
        *,
        pattern: str | tuple[str, ...] | list[str] = DEFAULT_PATTERNS,
        limit: int | None = None,
        batch_size: int | None = None,
        checkpoint_dir: str | Path | None = None,
        resume: bool = False,
        recursive: bool = False,
    ) -> nx.MultiDiGraph:

        data_dir = Path(data_dir)
        raw = [pattern] if isinstance(pattern, str) else list(pattern)
        globs = [g.strip() for item in raw for g in item.split(",") if g.strip()]
        finder = data_dir.rglob if recursive else data_dir.glob
        paths = sorted({p for g in globs for p in finder(g) if p.is_file()})
        if not paths:
            raise FileNotFoundError(f"no files matching {globs} in {data_dir.resolve()}")
        if limit is not None:
            paths = paths[:limit]
        kinds = Counter(p.suffix.lower() for p in paths)
        self._log(f"{len(paths)} document(s) from {data_dir} "
                  f"({', '.join(f'{n}{s}' for s, n in sorted(kinds.items()))})")
        return self.build(
            paths,
            batch_size=batch_size,
            checkpoint_dir=checkpoint_dir,
            resume=resume,
        )


    @staticmethod
    def save(graph: nx.MultiDiGraph, out_path: str | Path) -> Path:
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            data = nx.node_link_data(graph, edges="links")
        except TypeError:  # NetworkX < 3.4
            data = nx.node_link_data(graph)
            if "links" not in data and "edges" in data:
                data["links"] = data.pop("edges")
        # Write-then-rename: a crash partway through a checkpoint flush would otherwise
        # leave a truncated file where the resume data should be.
        tmp_path = out_path.with_name(out_path.name + ".tmp")
        tmp_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(tmp_path, out_path)
        return out_path

    @staticmethod
    def load(path: str | Path) -> nx.MultiDiGraph:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        try:
            return nx.node_link_graph(data, edges="links", directed=True, multigraph=True)
        except TypeError:  # NetworkX < 3.4
            return nx.node_link_graph(data, directed=True, multigraph=True)

    def _summarise(self, G: nx.MultiDiGraph) -> None:
        if not self.verbose:
            return
        unknown = [n for n, d in G.nodes(data=True) if d.get("type") == "unknown"]
        print(f"{len(unknown)} untyped (dangling) nodes: {unknown[:10]}")
        rules = [(u, v, d["label"]) for u, v, d in G.edges(data=True) if d.get("dashes")]
        print(f"{len(rules)} analytic-rule edges (not facts): {rules[:5]}")
        print("predicates:", Counter(d["label"] for _, _, d in G.edges(data=True)).most_common())
        print("node types:", Counter(d.get("type") for _, d in G.nodes(data=True)).most_common())


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Extract a knowledge graph from a corpus of PDF and/or text documents.",
    )
    parser.add_argument("data_dir", help="directory of documents")
    parser.add_argument("-o", "--out", default="kg_from_pdf.json", help="output graph JSON")
    parser.add_argument(
        "--pattern", action="append", default=None,
        help="glob to include; repeatable or comma-separated "
             f"(default: {','.join(DEFAULT_PATTERNS)})",
    )
    parser.add_argument("--recursive", action="store_true", help="search subdirectories")
    parser.add_argument(
        "--profile", default="auto", choices=("auto", *PROFILE_NAMES),
        help="prompt/example set: 'packet' for the PDF source packets, 'corpus' for "
             "Data/corpus documents, 'auto' to detect per document (default)",
    )
    parser.add_argument("--limit", type=int, default=None, help="max documents to process")
    parser.add_argument("--batch-size", type=int, default=None,
                        help="documents per checkpoint flush")
    parser.add_argument("--checkpoint-dir", default=None, help="dir for intermediate graphs")
    parser.add_argument("--resume", action="store_true",
                        help="continue from kg_checkpoint.json in --checkpoint-dir")
    parser.add_argument("--model-id", default="google/gemma-4-e4b")
    parser.add_argument("--base-url", default="http://127.0.0.1:1234/v1")
    parser.add_argument("--drop-analytic-rules", action="store_true")
    args = parser.parse_args()

    kg = PDFToKnowledgeGraph(
        model_id=args.model_id,
        base_url=args.base_url,
        profile=args.profile,
        drop_analytic_rules=args.drop_analytic_rules,
    )
    graph = kg.build_from_dir(
        args.data_dir,
        pattern=args.pattern or DEFAULT_PATTERNS,
        recursive=args.recursive,
        limit=args.limit,
        batch_size=args.batch_size,
        checkpoint_dir=args.checkpoint_dir,
        resume=args.resume,
    )
    path = kg.save(graph, args.out)
    print(f"wrote {path} ({path.stat().st_size:,} bytes)")


    
