import pytest

from scripts.verify_release_image import validate_image_reference


def test_release_image_requires_digest():
    digest = "a1" * 32
    assert validate_image_reference(f"ghcr.io/rlcuevas14/pulsyr@sha256:{digest}").endswith(digest)


@pytest.mark.parametrize(
    "reference",
    [
        "ghcr.io/rlcuevas14/pulsyr:latest",
        "ghcr.io/rlcuevas14/pulsyr:v1",
        "ghcr.io/rlcuevas14/pulsyr@sha256:" + "0" * 64,
        "ghcr.io/rlcuevas14/pulsyr@sha256:short",
    ],
)
def test_release_image_rejects_mutable_or_placeholder_references(reference):
    with pytest.raises(ValueError):
        validate_image_reference(reference)
