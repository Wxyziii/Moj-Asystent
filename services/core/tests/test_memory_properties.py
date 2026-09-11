from hypothesis import given
from hypothesis import strategies as st

from moj_asystent_core.memory import normalize_alias, normalize_key


@given(
    st.text(
        alphabet=st.characters(whitelist_categories=("Ll", "Lu", "Zs")),
        min_size=1,
        max_size=96,
    ).filter(lambda value: value.strip())
)
def test_memory_name_normalization_is_idempotent(value: str) -> None:
    alias = normalize_alias(value)
    assert normalize_alias(alias) == alias


@given(
    st.text(
        alphabet=st.characters(whitelist_categories=("Ll", "Lu")),
        min_size=1,
        max_size=96,
    )
)
def test_memory_keys_have_stable_normalization(value: str) -> None:
    normalized = normalize_key(value)
    assert normalize_key(normalized) == normalized
