"""Markdown renderers derived from embedding_clustering_experiment.

The DuckDB adapter retains the source layout while making non-canonical submitted
evidence explicit instead of silently treating it as comparable catalog evidence.
"""

from __future__ import annotations

import json
from typing import Any


def markdown_cell(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, (bool, dict, list)):
        rendered = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    else:
        rendered = str(value)
    return rendered.replace("|", "\\|").replace("\r\n", "<br>").replace("\n", "<br>")


def markdown_bold(value: Any) -> str:
    rendered = markdown_cell(value).replace("*", r"\*")
    return f"**{rendered}**"


def _fixed(value: Any, places: int = 3) -> str:
    return "—" if value is None else f"{float(value):.{places}f}"


def _rate_percent(value: float | None) -> str:
    return "—" if value is None else f"{float(value) * 100:.1f}%"


def _topic_particle(value: str) -> str:
    text = value.rstrip()
    if not text:
        return "는"
    codepoint = ord(text[-1])
    if 0xAC00 <= codepoint <= 0xD7A3:
        return "은" if (codepoint - 0xAC00) % 28 else "는"
    return "은"


def _directional_particle(value: str) -> str:
    text = value.rstrip()
    if not text:
        return "로"
    codepoint = ord(text[-1])
    if 0xAC00 <= codepoint <= 0xD7A3:
        final_consonant = (codepoint - 0xAC00) % 28
        return "로" if final_consonant in {0, 8} else "으로"
    return "로"


def _count_and_rate(count: int, rate: float | None) -> str:
    return f"{count}({_rate_percent(rate)})"


def _sentiment_cell(row: dict[str, Any], sentiment: str) -> str:
    distribution = row["sentiment"]
    return _count_and_rate(
        int(distribution["counts"][sentiment]), distribution["shares"][sentiment]
    )


def _top_negative_row(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    candidates = [row for row in rows if row["sentiment"]["counts"]["negative"]]
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda row: (
            row["sentiment"]["counts"]["negative"],
            float(row["sentiment"]["shares"]["negative"] or 0.0),
            row["supporting_review_count"],
            -row["rank"],
        ),
    )


def _render_review_samples(lines: list[str], samples: list[dict[str, Any]]) -> None:
    if not samples:
        lines.append("- 근거 없음")
        return
    for sample in samples:
        lines.append(f"- {markdown_cell(sample.get('review_text') or sample['excerpt'])}")


def _render_related_products_markdown(report: dict[str, Any]) -> str:
    related = report["related_products"]
    similar = related["similar_products"]
    alternatives = related["weakness_repair_alternatives"]
    lines = ["## 관련 상품", "", "### 유사 상품", ""]
    if similar:
        top = similar[0]
        lines.append(
            "본 상품과 리뷰 경험이 가장 유사한 제품은 리뷰 경험 유사도 "
            f"값 {_fixed(top['experience_similarity'], 6)}을 가지는 "
            f"{markdown_bold(top['product_name'])}입니다. 이는 experience similarity를 기반으로 "
            "evidence overlap과 support reliability를 고려하고 계산되었습니다."
        )
    else:
        lines.append("본 상품과 비교 가능한 리뷰 경험 유사 상품이 없습니다.")
    lines.extend(
        [
            "",
            "- **experience similarity**: 각 상품 aspect들을 긍정/부정/혼합/중립 값을 벡터화하여 코사인 유사도 값을 계산합니다.",
            "- **evidence overlap**: 공통적으로 가지는 aspect 비율.",
            "- **support reliability**: 공통 aspect의 관측 리뷰 수가 충분한지를 반영한 신뢰도.",
            "  - 높을수록 소수 리뷰의 우연한 감성보다 반복 관측된 근거에 기반합니다.",
            "",
            "| rank | product | experience similarity | evidence overlap | support reliability |",
            "|---:|---|---:|---:|---:|",
        ]
    )
    for row in similar:
        components = row["components"]
        lines.append(
            f"| {row['rank']} | {markdown_bold(row['product_name'])} | "
            f"{_fixed(row['experience_similarity'], 6)} | "
            f"{_fixed(components['evidence_overlap'], 6)} | "
            f"{_fixed(components['support_reliability'], 6)} |"
        )
    lines.extend(["", "### 관찰된 약점을 보완하는 대안 상품", ""])
    requirements = alternatives["source_weakness_requirements"]
    if requirements:
        requirement_text = ", ".join(
            f"'{item['aspect']} {item['status']}'" for item in requirements[:3]
        )
        lines.extend(
            [
                f"본 상품에는 {requirement_text}과 같은 부정적인 aspect-status 속성이 존재합니다.",
                "",
            ]
        )
    else:
        lines.extend(
            ["본 상품에는 support 2 이상으로 확인된 부정적인 aspect-status 속성이 없습니다.", ""]
        )
    if alternatives["alternatives"]:
        top = alternatives["alternatives"][0]
        lines.extend(
            [
                (
                    "부정 속성을 기피 조건으로 두고 약점을 보완할 수 있는 상품은 "
                    f"'{markdown_bold(top['product_name'])}'입니다. 이는 weakness utility과 "
                    "experience similarity를 3:1로 결합한 최종 점수인 weakness repair score를 "
                    "기반으로 계산되었습니다."
                ),
                "",
            ]
        )
    else:
        lines.extend(["근거가 확인된 약점 보완 후보가 없어 추천을 보류했습니다.", ""])
    lines.extend(
        [
            "- **weakness utility**: 원본의 부정 조건에 대해 후보가 얼마나 유리한지를 나타내는 근거 점수(-1~1로 정규화).",
            "- **experience similarity**: 상품의 유사성으로 약점 보완 순위가 어느 정도의 원본 경험 유사성을 유지하는지를 평가.",
            "- **weakness repair score**: weakness utility과 experience similarity를 3:1로 결합한 최종 점수로 비슷한 맥락에서 약점을 해결할 근거가 됩니다.",
            "",
            "| rank | product | weakness utility | experience similarity | weakness repair score |",
            "|---:|---|---:|---:|---:|",
        ]
    )
    for row in alternatives["alternatives"]:
        lines.append(
            f"| {row['rank']} | {markdown_bold(row['product_name'])} | "
            f"{_fixed(row['weakness_utility_score'], 6)} | "
            f"{_fixed(row['experience_similarity'], 6)} | "
            f"{_fixed(row['weakness_repair_score'], 6)} |"
        )
    return "\n".join(lines)


def render_static_markdown(report: dict[str, Any], *, row_limit: int = 10) -> str:
    product = report["product"]
    coverage = report["coverage"]
    reduction = report["normalization_reduction"]
    aspect_reduction = reduction["aspect"]
    status_reduction = reduction["status"]
    pair_reduction = reduction["aspect_status"]
    display_product_name = markdown_bold(product["product_name"])
    lines = [
        f"# {display_product_name} 상품에 대한 정적 카탈로그 분석 보고서",
        "",
        f"{display_product_name} 상품에 대한 정적 카탈로그 분석 보고서입니다.",
        "",
        f"{product['catalog_review_count']}개의 리뷰에서 {coverage['opinion_unit_count']}개의 Opinion Units 속성을 추출하였습니다.",
        "",
        (
            "군집화를 통해 기존 "
            f"{aspect_reduction['raw_count']}개였던 aspect의 가짓 수를 "
            f"{aspect_reduction['normalized_count']}개로 "
            f"{_rate_percent(aspect_reduction['decrease_rate'])} 감소시켰으며, aspect에 종속된 "
            f"status의 가짓 수를 {status_reduction['raw_count']}개에서 "
            f"{status_reduction['normalized_count']}개로 "
            f"{_rate_percent(status_reduction['decrease_rate'])} 감소시켰습니다. 최종적으로 "
            "aspect-status 조합의 고유 가짓 수는 "
            f"{_rate_percent(pair_reduction['decrease_rate'])} 감소했습니다."
        ),
    ]
    shown_aspects = report["aspect_summary"][:row_limit]
    top_aspect = shown_aspects[0] if shown_aspects else None
    negative_aspect = _top_negative_row(shown_aspects)
    lines.extend(
        [
            "",
            f"## 속성 감성 행렬 (상위 {row_limit}개)",
            "",
            "리뷰를 aspect 단위로 분석한 표입니다.",
            "",
            "- `etc`는 차례대로 mixed/neutral/unknown의 개수입니다.",
            "- `mention rate`의 분모는 해당 상품의 전체 카탈로그 리뷰입니다.",
            "",
            "| rank | aspect | reviews(mention rate) | positive(rate) | negative(rate) | etc |",
            "|---:|---|---:|---:|---:|---:|",
        ]
    )
    for row in shown_aspects:
        counts = row["sentiment"]["counts"]
        etc = f"{counts['mixed']}/{counts['neutral']}/{counts['unknown']}"
        lines.append(
            f"| {row['rank']} | {markdown_cell(row['aspect'])} | "
            f"{_count_and_rate(row['supporting_review_count'], row['mention_rate'])} | "
            f"{_sentiment_cell(row, 'positive')} | {_sentiment_cell(row, 'negative')} | {etc} |"
        )
    if top_aspect:
        lines.extend(
            [
                "",
                (
                    f"- 가장 넓게 언급된 aspect는 '{top_aspect['aspect']}'"
                    f"{_directional_particle(top_aspect['aspect'])} "
                    f"{top_aspect['supporting_review_count']}개의 리뷰에서 언급되었습니다."
                ),
            ]
        )
    if negative_aspect:
        lines.append(
            f"- 부정 리뷰가 가장 많은 aspect는 '{negative_aspect['aspect']}'"
            f"{_directional_particle(negative_aspect['aspect'])} "
            f"{negative_aspect['sentiment']['counts']['negative']}개의 리뷰에서 언급되었습니다."
        )
    elif shown_aspects:
        lines.append("- 상위 표시 aspect에는 부정 리뷰가 없습니다.")
    shown_pairs = report["aspect_status_summary"][:row_limit]
    negative_pair = _top_negative_row(report["aspect_status_summary"])
    lines.extend(
        [
            "",
            f"## 속성-상태 행렬 (상위 {row_limit}개)",
            "",
            "리뷰를 aspect와 status 조합 단위로 분석한 표입니다.",
            "",
            "- `positive wilson lower`는 표본 수를 보수적으로 반영한 긍정 비율의 95% Wilson 하한입니다.",
            "- `etc`는 차례대로 mixed/neutral/unknown의 개수입니다.",
            "",
            "| rank | aspect > status | reviews(mention rate) | positive(rate) | negative(rate) | etc | positive wilson lower |",
            "|---:|---|---:|---:|---:|---:|---:|",
        ]
    )
    for row in shown_pairs:
        counts = row["sentiment"]["counts"]
        etc = f"{counts['mixed']}/{counts['neutral']}/{counts['unknown']}"
        aspect_status = f"{row['aspect']} > {row['status'] if row['status'] is not None else '—'}"
        lines.append(
            f"| {row['rank']} | {markdown_cell(aspect_status)} | "
            f"{_count_and_rate(row['supporting_review_count'], row['mention_rate'])} | "
            f"{_sentiment_cell(row, 'positive')} | {_sentiment_cell(row, 'negative')} | {etc} | "
            f"{_fixed(row['sentiment']['positive_wilson_95']['lower'], 6)} |"
        )
    if shown_pairs:
        top_three = ", ".join(
            f"'{row['aspect']} > {row['status'] if row['status'] is not None else '—'}' ({row['supporting_review_count']}개)"
            for row in shown_pairs[:3]
        )
        lines.extend(["", f"- 가장 많이 언급된 제품 속성은 {top_three}입니다."])
    if negative_pair:
        negative_pair_label = (
            f"{negative_pair['aspect']} > "
            f"{negative_pair['status'] if negative_pair['status'] is not None else '—'}"
        )
        lines.append(
            "- 전체 aspect-status 조합에서 부정 리뷰가 가장 많은 항목은 "
            f"'{negative_pair_label}'{_directional_particle(negative_pair_label)} "
            f"{negative_pair['sentiment']['counts']['negative']}개의 리뷰에서 언급되었습니다."
        )
    elif shown_pairs:
        lines.append("- 전체 aspect-status 조합에 부정 리뷰가 없습니다.")
    most_debated = report["most_debated_aspect"]
    lines.extend(["", "## 가장 논쟁적인 속성", ""])
    if most_debated:
        sentiment = most_debated["sentiment"]
        lines.extend(
            [
                (
                    f"{most_debated['aspect']}{_topic_particle(most_debated['aspect'])} 긍정 "
                    f"{_rate_percent(sentiment['shares']['positive'])}, "
                    f"부정 {_rate_percent(sentiment['shares']['negative'])}로 상위 10개의 aspect 중 "
                    "가장 작은 긍정/부정 비율 차이인 "
                    f"{_rate_percent(most_debated['positive_negative_rate_gap'])}를 보이는 "
                    "가장 논쟁적인 aspect입니다."
                ),
                "",
                "### 긍정 리뷰 샘플",
                "",
            ]
        )
        _render_review_samples(lines, most_debated["evidence"]["positive"])
        lines.extend(["", "### 부정 리뷰 샘플", ""])
        _render_review_samples(lines, most_debated["evidence"]["negative"])
    else:
        lines.append(
            "상위 10개의 aspect에 긍정 또는 부정 표본이 없어 논쟁적인 aspect를 계산하지 않았습니다."
        )
    lines.extend(["", _render_related_products_markdown(report)])
    return "\n".join(lines).replace("_", " ") + "\n"


def _review_display_blocks(submission: dict[str, Any]) -> list[str]:
    return [f'"{markdown_cell(review["review"])}"' for review in submission["reviews"]]


def _relationship_heading(relationship: dict[str, Any]) -> str:
    return " ".join(
        value
        for value in (markdown_bold(relationship["aspect"]), relationship["status"])
        if value is not None
    )


def _render_other_aspect_top_statuses(lines: list[str], relationship: dict[str, Any]) -> None:
    aspect = markdown_bold(relationship["aspect"])
    top_statuses = relationship["other_aspect_top_statuses"]
    lines.extend(
        [
            f"제출된 리뷰를 제외한 다른 리뷰에는 {aspect} > {relationship['status']} 조합과 완전히 일치하는 aspect-status가 없습니다.",
            "",
        ]
    )
    if not top_statuses:
        lines.append(f"제출된 리뷰를 제외한 다른 리뷰에는 {aspect}의 status 관찰 자체가 없습니다.")
        return
    lines.extend(
        [
            f"대신 제출된 리뷰를 제외한 {aspect}의 가장 많이 언급된 status Top 3는 다음과 같습니다.",
            "",
            "| rank | status | reviews | positive | negative | etc |",
            "|---:|---|---:|---:|---:|---|",
        ]
    )
    for row in top_statuses:
        counts = row["sentiment"]["counts"]
        lines.append(
            f"| {row['rank']} | {markdown_cell(row['status'])} | "
            f"{row['supporting_review_count']} | {counts['positive']} | "
            f"{counts['negative']} | {counts['mixed']}/{counts['neutral']}/{counts['unknown']} |"
        )


def _render_relationship(lines: list[str], relationship: dict[str, Any]) -> None:
    lines.extend(["", f"### {_relationship_heading(relationship)}", ""])
    aspect = markdown_bold(relationship["aspect"])
    if relationship["comparison_grain"] == "aspect_status":
        lines.extend(
            [
                (
                    f"전체 리뷰 {relationship['catalog_review_count']}개 중 "
                    f"{aspect} 관련 언급은 {relationship['aspect_review_count']}개 리뷰에서 관찰됐습니다. "
                    f"이 중 {aspect} > {relationship['status']} 상태는 "
                    f"{relationship['same_status_review_count']}개 리뷰에서 관찰됐습니다."
                ),
                "",
            ]
        )
        if relationship["other_status_review_count"] == 0:
            _render_other_aspect_top_statuses(lines, relationship)
            lines.extend(
                [
                    "",
                    "따라서 제출된 리뷰의 평가는 다른 리뷰 다수와의 방향 일치를 판단하기 어렵습니다.",
                ]
            )
        else:
            lines.append(
                f"제출된 리뷰를 제외하고 동일한 {aspect} > {relationship['status']} 조합을 언급한 리뷰 중 "
                f"{relationship['other_status_sentiment']['counts']['negative']}개는 부정, "
                f"{relationship['other_status_sentiment']['counts']['positive']}개는 긍정, "
                f"{relationship['other_status_sentiment']['counts']['mixed']}개는 혼합 평가였습니다. "
                f"제출된 리뷰의 평가는 다수 리뷰의 평가 방향과 '{relationship['relation_label']}'."
            )
    else:
        lines.extend(
            [
                f"전체 리뷰 {relationship['catalog_review_count']}개 중 {aspect} 관련 언급은 {relationship['aspect_review_count']}개 리뷰에서 관찰됐습니다.",
                "",
                f"제출된 Opinion Unit에 상태가 없어 {aspect} 단위로만 비교했습니다. 제출된 리뷰의 평가는 다수 리뷰의 평가 방향과 '{relationship['relation_label']}'.",
            ]
        )


def _render_alternatives(lines: list[str], alternatives: dict[str, Any]) -> None:
    lines.extend(["", "## 대안 상품 추천", ""])
    conditions = alternatives["negative_conditions"]
    excluded = alternatives["excluded_negative_conditions"]
    ranked = alternatives["alternatives"]
    if alternatives["status"] != "COMPLETED":
        if alternatives["status"] == "NO_NEGATIVE_SUBMITTED_ASPECT_STATUS":
            lines.append(
                "제출된 리뷰에서 부정적인 상품 속성-상태가 관찰되지 않아 대안 상품을 추천하지 않습니다."
            )
        elif alternatives["status"] == "NEGATIVE_INPUT_UNRESOLVED":
            lines.append(
                f"제출된 리뷰에서 부정적인 Opinion Unit {alternatives['observed_negative_opinion_unit_count']}개가 관찰됐지만, "
                "정확한 mapping table match가 없어 canonical aspect-status로 확정되지 않았습니다. "
                "candidate 추정값은 근거 기반 대안 순위에 사용하지 않으므로 추천을 보류합니다."
            )
            _render_unresolved_negative_conditions(lines, excluded)
        else:
            lines.append("부정 속성을 보완할 수 있는 카탈로그 근거가 있는 대안 상품이 없습니다.")
            _render_unresolved_negative_conditions(lines, excluded)
        return
    labels = ", ".join(f"'{item['aspect']} {item['status']}'" for item in conditions)
    top = ranked[0]
    lines.extend(
        [
            f"제출된 리뷰에서 확인된 부정적인 속성-상태는 {labels}입니다.",
            "",
            (
                "제출된 부정 속성을 기피 조건으로 둘 때, 1순위 대안은 "
                f"{markdown_bold(top['product_name'])}입니다. "
                f"**weakness utility** {top['weakness_utility']:.6f} 및 "
                f"**experience similarity** {top['experience_similarity']:.6f}를 3:1로 결합한 "
                f"**weakness repair score** {top['weakness_repair_score']:.6f}가 후보 중 가장 높기 때문입니다."
            ),
            "",
            "정적 카탈로그 분석의 약점 보완 방식을 적용해, 제출된 부정 조건에 대한 weakness utility와 원본 상품의 리뷰 경험 유사도를 3:1로 결합했습니다.",
            "",
            "- **weakness utility**: 제출된 부정 조건에 대해 후보가 얼마나 유리한지를 나타내는 근거 점수(-1~1).",
            "- **experience similarity**: 원본 상품과의 리뷰 경험 유사도로, 약점 보완이 얼마나 유사한 맥락을 유지하는지 나타냅니다.",
            "- **weakness repair score**: weakness utility와 experience similarity를 3:1로 결합한 최종 순위 점수입니다.",
            "",
            "| rank | product | weakness utility | experience similarity | weakness repair score | recommendation reason |",
            "|---:|---|---:|---:|---:|---|",
        ]
    )
    for row in ranked:
        lines.append(
            f"| {row['rank']} | {markdown_bold(row['product_name'])} | "
            f"{row['weakness_utility']:.6f} | {row['experience_similarity']:.6f} | "
            f"{row['weakness_repair_score']:.6f} | {markdown_cell(row['recommendation_reason'])} |"
        )
    _render_unresolved_negative_conditions(lines, excluded)


def _render_unresolved_negative_conditions(
    lines: list[str], excluded: list[dict[str, Any]]
) -> None:
    if not excluded:
        return
    lines.extend(
        [
            "",
            "### 추천 조건에서 보류된 부정 Opinion Unit",
            "",
            "아래 Unit은 부정 경험으로 추출됐지만 canonical aspect-status가 없어 대안 순위의 기피 조건에 포함하지 않았습니다.",
            "",
            "| raw_aspect | raw_status | sentiment | mapping state | suggested aspect | suggested status | reason |",
            "|---|---|---|---|---|---|---|",
        ]
    )
    for row in excluded:
        lines.append(
            f"| {markdown_cell(row.get('raw_aspect') or row.get('aspect'))} | "
            f"{markdown_cell(row.get('raw_status') or row.get('status'))} | "
            f"{markdown_cell(row.get('sentiment'))} | {markdown_cell(row.get('mapping_state'))} | "
            f"{markdown_cell(row.get('suggested_aspect'))} | "
            f"{markdown_cell(row.get('suggested_status'))} | {markdown_cell(row['reason'])} |"
        )


def _render_unresolved_catalog_comparison(lines: list[str], comparison: dict[str, Any]) -> None:
    unresolved = comparison["unresolved_opinion_units"]
    if not unresolved:
        return
    lines.extend(
        [
            "",
            "### 다른 리뷰와의 비교가 보류된 Opinion Unit",
            "",
            "아래 Unit은 mapping table exact match가 없어 canonical aspect-status로 확정되지 않았습니다. "
            "따라서 다른 리뷰에서 언급됐는지 여부를 판정하지 않았습니다.",
            "",
            "| raw_aspect | raw_status | sentiment | mapping state | suggested aspect | suggested status | reason |",
            "|---|---|---|---|---|---|---|",
        ]
    )
    for row in unresolved:
        lines.append(
            f"| {markdown_cell(row['raw_aspect'])} | {markdown_cell(row['raw_status'])} | "
            f"{markdown_cell(row['sentiment'])} | {markdown_cell(row['mapping_state'])} | "
            f"{markdown_cell(row['suggested_aspect'])} | "
            f"{markdown_cell(row['suggested_status'])} | {markdown_cell(row['reason'])} |"
        )


def render_dynamic_review_decision_markdown(proposal: dict[str, Any]) -> str:
    submission = proposal["submission"]
    lines = [
        "# 동적 의사결정 제안서",
        "",
        f"{submission['submitted_at_local']}에 제출된 {markdown_bold(submission['product_name'])}에 대한 리뷰",
        "",
        *_review_display_blocks(submission),
        "",
        "를 기반으로 의사결정을 보조하는 제안서입니다.",
        "",
        "제출된 리뷰에서 추출 및 정규화된 속성은 다음과 같습니다.",
        "",
        "| raw_aspect | aspect | raw_status | status | excerpt | opinion | sentiment |",
        "|---|---|---|---|---|---|---|",
    ]
    for unit in proposal["submitted_opinion_units"]:
        lines.append(
            f"| {markdown_cell(unit['raw_aspect'])} | {markdown_cell(unit['aspect'])} | "
            f"{markdown_cell(unit['raw_status'])} | {markdown_cell(unit['status'])} | "
            f"{markdown_cell(unit['excerpt'])} | {markdown_cell(unit['opinion'])} | "
            f"{markdown_cell(unit['sentiment'])} |"
        )
    lines.extend(
        [
            "",
            "- **raw_aspect**: 추출 시점에 정규화하지 않고 기록한, 리뷰어가 평가한 상품 속성·구성요소·사용 상황의 원본 명칭.",
            "- **aspect**: raw_aspect를 그대로 쓰거나 같은 product category 안의 군집 대표명으로 정규화한 최종 분석 속성.",
            "- **raw_status**: raw_aspect의 관찰된 상태·조건·값이며, 근거 있는 상태를 특정할 수 없을 때만 null을 가집니다.",
            "- **status**: raw_status를 그대로 쓰거나 해당 aspect 군집 안의 군집 대표명으로 정규화한 최종 분석 상태값.",
            "- **excerpt**: 해당 Opinion Unit을 뒷받침하도록 리뷰 원문에서 변경 없이 복사한 연속 구간.",
            "- **opinion**: 방향·정도·비교·사용 맥락을 보존해 리뷰어의 관찰 또는 평가를 간결하게 완결한 서술.",
            "- **sentiment**: 해당 aspect·status에 대한 리뷰어의 평가 방향으로 positive, negative, mixed, neutral, unknown 중 하나의 값을 가집니다.",
            "",
            "배송 상태와 같이 상품과 관련되지 않은 속성은 추출하지 않으며, 특별한 상품 속성 없는 긍정/부정 리뷰는 '전반적 상품 경험'이라는 속성값을 가집니다.",
            "",
            "## 다른 리뷰와의 관계",
        ]
    )
    if proposal["catalog_relationships"]:
        for relationship in proposal["catalog_relationships"]:
            _render_relationship(lines, relationship)
    else:
        lines.extend(
            [
                "",
                "제출된 Opinion Unit이 카탈로그의 정규화된 상품 속성과 연결되지 않아 비교할 수 없습니다.",
            ]
        )
    _render_unresolved_catalog_comparison(lines, proposal["catalog_comparison"])
    _render_alternatives(lines, proposal["alternative_recommendations"])
    return "\n".join(lines) + "\n"
