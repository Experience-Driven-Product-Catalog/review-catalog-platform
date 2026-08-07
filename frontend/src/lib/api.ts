export type Product = {
  product_id: string
  product_name: string
  category: string
  review_count: number
}

export type OpinionUnit = {
  raw_aspect: string
  raw_status: string | null
  aspect: string | null
  status: string | null
  sentiment: string
  mapping_state: string
  suggested_aspect: string | null
  aspect_distance: number | null
  aspect_membership_max_distance: number | null
  aspect_centroid_distance: number | null
  aspect_second_nearest_distance: number | null
  aspect_distance_margin: number | null
  aspect_candidate_eligible: boolean | null
  suggested_status: string | null
  status_distance: number | null
  status_membership_max_distance: number | null
  status_centroid_distance: number | null
  status_second_nearest_distance: number | null
  status_distance_margin: number | null
  status_candidate_eligible: boolean | null
  normalization_run_id: string
  excerpt: string
  opinion: string
}

export type SubmissionStatus = {
  submission_id: string
  pipeline_run_id: string
  product_id: string
  state: string
  release_id: string | null
  error_message: string | null
  reviews: Array<{ demo_review_id: string; position: number; review: string }>
  results: Array<{
    demo_review_id: string
    opinion_units: OpinionUnit[]
    proposal_markdown: string | null
  }>
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, init)
  if (!response.ok) {
    const payload = await response.json().catch(() => ({ detail: response.statusText }))
    throw new Error(payload.detail ?? response.statusText)
  }
  const contentType = response.headers.get('content-type') ?? ''
  return (contentType.includes('text/markdown')
    ? await response.text()
    : await response.json()) as T
}

export const api = {
  about: () => request<{ markdown: string }>('/api/about'),
  aboutSection: (section: string) =>
    request<{ markdown: string; source_url: string | null }>(
      `/api/about/${encodeURIComponent(section)}`,
    ),
  products: () => request<Product[]>('/api/products'),
  productReport: (productId: string) =>
    request<string>(`/api/products/${encodeURIComponent(productId)}/report`),
  submitReviews: (productId: string, reviews: string[]) =>
    request<{ submission_id: string; pipeline_run_id: string; state: string }>(
      '/api/demo/submissions',
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ product_id: productId, reviews }),
      },
    ),
  submission: (submissionId: string) =>
    request<SubmissionStatus>(`/api/demo/submissions/${submissionId}`),
}
