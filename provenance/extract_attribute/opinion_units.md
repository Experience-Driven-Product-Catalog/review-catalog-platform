Perform open-ended aspect-based opinion extraction for the product review provided as input.

Identify every valid observation, experience, effect, or evaluation that the reviewer expresses about the reviewed product or the experience of using it. Return each independently assessable statement as one opinion unit.

Each opinion unit has exactly these five fields:

1. `raw_aspect`: the product attribute, component, behavior, consequence, or usage situation being evaluated;
2. `raw_status`: the state, condition, manifestation, or value of that aspect, or `null` only when no grounded state can be named;
3. `excerpt`: an exact contiguous substring copied from the review;
4. `opinion`: a concise statement preserving the reviewer's complete observation or evaluation;
5. `sentiment`: the reviewer's evaluation direction for this specific aspect and status.

Create `raw_aspect` and `raw_status` dynamically. Do not restrict them to a predefined taxonomy.

## Aspect and status

`raw_aspect` identifies what is evaluated. `raw_status` identifies the state it is in.

Use concise, reusable noun phrases. Keep state, polarity, degree, comparison, time, cause, and final judgment out of `raw_aspect`.

Examples:

- use `raw_aspect: 키압`, `raw_status: 무거움`, not `raw_aspect: 무거운 키압`;
- use `raw_aspect: 스템 흔들림`, `raw_status: 적음`, not `raw_aspect: 적은 스템 흔들림`;
- use `raw_aspect: 오입력`, `raw_status: 발생`, not `raw_aspect: 잦은 오입력`;
- use `raw_aspect: 장시간 타이핑`, `raw_status: 편안함`, not `raw_aspect: 편안한 장시간 타이핑`.

Prefer direct labels such as `키압`, `스프링 소리`, `오입력`, `키캡 촉감`, and `장시간 타이핑`. Avoid unnecessarily broad labels such as `품질`, `성능`, `특성`, or `사용 경험` when the review supports a concrete target.

Use `전반적 상품 경험` only for an explicit whole-product evaluation that cannot be assigned to a specific attribute. It is not a fallback for a difficult-to-name aspect.

`raw_status` should capture the core state without absorbing details that belong in `opinion`. Normally keep degree, comparison target, usage context, time, cause, and consequences in `opinion`.

For example, from `적축보다 키압이 조금 무겁지만 저는 묵직해서 마음에 듭니다.`, use:

- `raw_aspect`: `키압`;
- `raw_status`: `무거움`;
- `opinion`: `적축보다 키압이 조금 무겁지만 묵직한 느낌을 선호함`.

Do not replace a concrete status with `긍정`, `부정`, `좋음`, or `나쁨`. A state is not itself a sentiment: `무거움`, `가벼움`, `발생`, and `없음` may be liked, disliked, or neutrally observed depending on the review.

Preserve uncertainty in the status when removing it would be misleading. For example, use `적합 가능성`, `불확실`, or `판단 보류` when those meanings are expressed.

## Sentiment

`sentiment` must be exactly one of these five labels and must describe the reviewer's direction toward this opinion unit, not a generic prior about the state:

- `positive`: the reviewer clearly prefers or is satisfied with the aspect or state;
- `negative`: the reviewer is clearly dissatisfied with the aspect or state;
- `mixed`: advantages and disadvantages of the same aspect are explicitly weighed together in one inseparable evaluation;
- `neutral`: the reviewer states a fact or experience without evaluating it;
- `unknown`: the evidence is insufficient or too uncertain to determine an evaluation direction.

Important distinctions:

- `키압이 무겁지만 묵직해서 마음에 든다` is `positive`, not automatically `negative`.
- `키압이 무거워서 손이 아프다` is `negative`.
- `키압은 45g으로 느껴진다` is `neutral` when no preference is expressed.
- `키압은 아직 잘 모르겠다` is `unknown`, not `neutral`.
- Use `mixed` only when the review explicitly presents a trade-off for the same aspect and a single combined evaluation best preserves that trade-off.
- If positive and negative statements concern different aspects, create separate units and label each one independently rather than using `mixed`.
- Tentative wording can still have a clear direction. For example, `게임용으로 괜찮을 것 같다` is generally `positive` while preserving the uncertainty in `raw_status` and `opinion`.

## Attribution and grounding

Extract only the reviewer's own experience, direct observation, or explicit evaluation of the reviewed product.

Do not treat another person's opinion, a seller or manufacturer claim, marketing text, a category stereotype, or an evaluation applying only to a comparison product as the reviewer's observation.

When the reviewed product is compared with another product, describe only the reviewed product and preserve the comparison target in `opinion`.

The product name and product category are context only. Use them to understand terminology and references, but never treat information appearing only in metadata as review evidence.

Do not infer unmentioned technical causes, features, states, or evaluations.

## Opinion-unit boundaries

Create separate units for different aspects. A sentence may produce several units, and those units may share the same excerpt when contrast, comparison, negation, causality, usage conditions, or time must be preserved.

For the same aspect:

- create separate units when independently useful states, usage contexts, or time periods are evaluated separately;
- combine them only when the reviewer weighs them as one inseparable trade-off, using `mixed`;
- do not duplicate repeated statements expressing the same observation;
- use consistent aspect and status labels for the same concepts within a review.

An experienced consequence may be its own aspect only when it is independently stated and useful. Do not invent a consequence or technical cause.

## Excerpt and opinion

Every `excerpt` must:

- be copied exactly without paraphrasing;
- be one contiguous substring of `<review>`;
- contain enough text to preserve target, state, comparison, negation, degree, context, attribution, uncertainty, and consequence;
- include multiple sentences when they are required for the meaning.

The `opinion` must preserve the complete proposition concisely, including relevant direction, degree, comparison, usage context, temporal context, consequence, uncertainty, and limited experience. Do not reduce it to only the aspect and status or to only a sentiment label.

## Inclusion and exclusion

Include product-use facts and sensory, ergonomic, behavioral, visual, or functional observations even without explicit praise or criticism; label those `neutral` when the direction is genuinely absent.

Exclude shipping, packaging, seller service, payment, delivery issues, promotions, free gifts, and content unrelated to the product itself or its use. A logistics-only review returns an empty array even if the logistics are praised or criticized.

Do not include objective product facts unless the reviewer reports or evaluates them as part of their experience.

If the review contains no valid product-related observation or evaluation, return `{ "opinion_units": [] }`.

Use the same language as the review for `raw_aspect`, `raw_status`, and `opinion`.

Return valid JSON only. Do not return Markdown, explanations, headings, or code fences. Use exactly this envelope and no extra fields:

{
  "opinion_units": [
    {
      "raw_aspect": "속성 명칭",
      "raw_status": "속성의 상태 또는 null",
      "excerpt": "리뷰에서 그대로 복사한 연속 근거",
      "opinion": "리뷰어가 표현한 완전한 관찰 또는 평가",
      "sentiment": "positive | negative | mixed | neutral | unknown"
    }
  ]
}

## Few-shot example 1: multiple monitor attributes

Product name:

기계식 키보드

Product category:

키보드

Review:

<review>
적축보다 키압이 조금 무겁지만 저는 묵직해서 마음에 듭니다. 장시간 타이핑할 때 손이 편했고, 스템 흔들림은 꽤 거슬렸습니다.
</review>

Output:

{
  "opinion_units": [
    {
      "raw_aspect": "키압",
      "raw_status": "무거움",
      "excerpt": "적축보다 키압이 조금 무겁지만 저는 묵직해서 마음에 듭니다.",
      "opinion": "적축보다 키압이 조금 무겁지만 묵직한 느낌을 선호함",
      "sentiment": "positive"
    },
    {
      "raw_aspect": "장시간 타이핑",
      "raw_status": "편안함",
      "excerpt": "장시간 타이핑할 때 손이 편했고, 스템 흔들림은 꽤 거슬렸습니다.",
      "opinion": "장시간 타이핑할 때 손이 편안함",
      "sentiment": "positive"
    },
    {
      "raw_aspect": "스템 흔들림",
      "raw_status": "거슬림",
      "excerpt": "장시간 타이핑할 때 손이 편했고, 스템 흔들림은 꽤 거슬렸습니다.",
      "opinion": "스템 흔들림이 꽤 거슬림",
      "sentiment": "negative"
    }
  ]
}

## Few-shot example 2: mixed, neutral, unknown, and exclusion

Product name:

무선 기계식 키보드

Product category:

키보드

Review:

<review>
배송은 빨랐습니다. 키캡 촉감은 부드러워서 좋지만 손에 땀이 나면 미끄러운 점은 아쉽습니다. 키압은 45g 정도로 느껴집니다. 스위치 내구성은 더 써봐야 알 것 같습니다.
</review>

Output:

{
  "opinion_units": [
    {
      "raw_aspect": "키캡 촉감",
      "raw_status": "부드럽지만 미끄러움",
      "excerpt": "키캡 촉감은 부드러워서 좋지만 손에 땀이 나면 미끄러운 점은 아쉽습니다.",
      "opinion": "키캡 촉감은 부드러워서 좋지만 손에 땀이 나면 미끄러워 아쉬움",
      "sentiment": "mixed"
    },
    {
      "raw_aspect": "키압",
      "raw_status": "45g 정도",
      "excerpt": "키압은 45g 정도로 느껴집니다.",
      "opinion": "키압이 45g 정도로 느껴짐",
      "sentiment": "neutral"
    },
    {
      "raw_aspect": "스위치 내구성",
      "raw_status": "판단 보류",
      "excerpt": "스위치 내구성은 더 써봐야 알 것 같습니다.",
      "opinion": "스위치 내구성은 더 사용해 봐야 판단할 수 있음",
      "sentiment": "unknown"
    }
  ]
}

### Input

Product name:

{{product_name}}

Product category:

{{product_category}}

Review:

<review>
{{review}}
</review>

### Output

