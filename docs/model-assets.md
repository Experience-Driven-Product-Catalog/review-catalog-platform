# Local embedding model asset

The 447 MiB KR-SBERT `model.safetensors` file is intentionally excluded from the public Git repository and GitHub Actions artifacts. It is not a credential, but it exceeds GitHub's normal file-size limit and should remain an immutable runtime asset.

Expected runtime directory:

```text
models/snunlp--KR-SBERT-Medium-extended-klueNLItriplet_PARpair_QApair-klueSTS/
```

The expected artifact SHA-256 is recorded in `config/taxonomy/20260803-213339.json`. The EC2 deployment restores the model from the private, versioned bootstrap artifact in S3 and exposes it to each application release through `/opt/review-catalog-platform/shared/models`.

Local development must place a compatible Hugging Face snapshot at the same path. Do not commit Hugging Face caches or model binaries to this repository.

Manual reclustering re-encodes every captured taxonomy expression with this same immutable model. Each run creates an `embedding_model_manifest.json` and a new logical component version tied to the reclustering run, while reusing the verified weight artifact SHA-256. The large weight file is not retrained or duplicated per run.
