# Opinion Unit JSON 필드 안내

이 디렉터리의 `{review_idx}.json`은 원본 Opinion Unit 추출 결과를 저장한다. JSON에는 `raw_aspect`, `raw_status`, `excerpt`, `opinion`, `sentiment`만 기록되고, `aspect`와 `status`는 후속 군집화·정규화 매핑에서 생성된다.

| 필드 | 한 줄 설명 |
| --- | --- |
| `raw_aspect` | 추출 시점에 정규화하지 않고 기록한, 리뷰어가 평가한 상품 속성·구성요소·사용 상황의 원본 명칭이다. |
| `raw_status` | `raw_aspect`의 관찰된 상태·조건·값이며, 근거 있는 상태를 특정할 수 없을 때만 `null`이다. |
| `aspect` | `raw_aspect`를 그대로 쓰거나 같은 `product_category` 안의 군집 대표명으로 정규화한 최종 분석 속성이다. |
| `status` | `raw_status`를 그대로 쓰거나 해당 aspect 군집 안의 군집 대표명으로 정규화한 최종 분석 상태다. |
| `excerpt` | 해당 Opinion Unit을 뒷받침하도록 리뷰 원문에서 변경 없이 복사한 연속 구간이다. |
| `opinion` | 방향·정도·비교·사용 맥락을 보존해 리뷰어의 관찰 또는 평가를 간결하게 완결한 서술이다. |
| `sentiment` | 해당 aspect·status에 대한 리뷰어의 평가 방향으로 `positive`, `negative`, `mixed`, `neutral`, `unknown` 중 하나다. |
