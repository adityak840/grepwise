from pathlib import Path

import pytest

from grepwise.ingest.chunkers import MAX_CHUNK_CHARS, split_lines
from grepwise.ingest.chunkers.openapi import TRUNCATED, chunk_endpoint, render_operations
from grepwise.ingest.openapi import OpenAPIError, load_spec, parse_spec

OA = Path(__file__).parent / "fixtures" / "openapi"


@pytest.fixture(scope="module")
def endpoints() -> dict[str, str]:
    return {e.path: e.content for e in render_operations(load_spec(str(OA / "petstore.json")))}


def test_one_virtual_document_per_operation(endpoints: dict[str, str]) -> None:
    assert list(endpoints) == [
        "PUT /pet",
        "POST /pet",
        "GET /pet/findByStatus",
        "GET /pet/{petId}",
        "DELETE /pet/{petId}",
        "POST /store/order",
        "GET /categories/{categoryId}",
        "GET /deep",
        "GET /reports/big",
    ]


def test_get_pet_by_id_rendering(endpoints: dict[str, str]) -> None:
    assert (
        endpoints["GET /pet/{petId}"]
        == """\
GET /pet/{petId}
Operation: getPetById
Summary: Find pet by ID
Tags: pet

Description:
Returns a single pet

Parameters:
- petId (path, required, integer<int64>): ID of pet to return

Responses:
- 200: successful operation
  Pet
    id (integer<int64>)
    name (string, required): The pet's name
    category (Category)
      id (integer<int64>)
      name (string)
      parent (Category)
        → Category
    photoUrls (array of string, required)
    tags (array of Tag)
      id (integer)
      name (string)
    status (string enum: available, pending, sold): pet status in the store
- 404: Pet not found
"""
    )


def test_refs_to_request_bodies_and_responses_are_resolved(endpoints: dict[str, str]) -> None:
    put = endpoints["PUT /pet"]
    assert "Request body (application/json):\n  Pet\n    id (integer<int64>)" in put
    assert "- 200: Order placed\n  Order\n    id" in endpoints["POST /store/order"]


def test_circular_reference_shows_arrow(endpoints: dict[str, str]) -> None:
    assert "parent (Category)\n      → Category" in endpoints["GET /categories/{categoryId}"]


def test_refs_deeper_than_three_show_arrow(endpoints: dict[str, str]) -> None:
    assert "b (B)\n      c (C)\n        d (D)\n          → D\n" in endpoints["GET /deep"]


def test_huge_schema_is_truncated(endpoints: dict[str, str]) -> None:
    big = endpoints["GET /reports/big"]
    assert big.endswith(f"\n{TRUNCATED}\n")
    assert len(big) <= MAX_CHUNK_CHARS


def test_misc_fields(endpoints: dict[str, str]) -> None:
    assert "Deprecated: yes" in endpoints["DELETE /pet/{petId}"]
    assert "- petId (path, required" in endpoints["DELETE /pet/{petId}"]  # path-level param
    found = endpoints["GET /pet/findByStatus"]
    assert "Description:\nMultiple status values can be provided.\nUse commas" in found
    assert "- status (query, string enum: available, pending, sold)" in found
    assert "  array of Pet\n    id" in found


def test_yaml_and_json_give_identical_results() -> None:
    from_json = render_operations(load_spec(str(OA / "petstore.json")))
    from_yaml = render_operations(load_spec(str(OA / "petstore.yaml")))
    assert from_json == from_yaml


def test_endpoint_chunk_covers_the_whole_rendering(endpoints: dict[str, str]) -> None:
    for e in render_operations(load_spec(str(OA / "petstore.json"))):
        chunk = chunk_endpoint(e)
        assert (chunk.kind, chunk.symbol, chunk.start_line) == ("endpoint", e.path, 1)
        assert chunk.end_line == len(split_lines(e.content))
        assert chunk.content == e.content


def test_rejects_swagger_2() -> None:
    with pytest.raises(OpenAPIError, match=r"Only OpenAPI 3\.x.*swagger 2\.0"):
        load_spec(str(OA / "swagger2.json"))


@pytest.mark.parametrize("raw", [b"not: [valid", b"[1, 2]", b'{"openapi": "3.0.0"}'])
def test_rejects_bad_specs(raw: bytes) -> None:
    with pytest.raises(OpenAPIError):
        parse_spec(raw)


def test_missing_file() -> None:
    with pytest.raises(OpenAPIError, match="No such file"):
        load_spec("/nonexistent/spec.json")


def test_self_referencing_alias_cannot_loop() -> None:
    spec = {
        "openapi": "3.0.0",
        "paths": {"/x": {"get": {"responses": {"200": {"$ref": "#/components/responses/R"}}}}},
        "components": {"responses": {"R": {"$ref": "#/components/responses/R"}}},
    }
    assert render_operations(spec)[0].content == "GET /x\n"  # unresolvable: section omitted
