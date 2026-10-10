"""Personal-search plan coordination before the existing search engines execute."""

from __future__ import annotations

from datetime import date
from typing import Any

from photo_search.persons import collections as person_collections
from photo_search.places import choices as place_choices
from photo_search.search.intent_plan import validate_plan_shape
from photo_search.search.unified_intent import parse_unified_intent


class PersonalSearchCoordinator:
    """Own the original-text plan and revalidate catalog identities at the API boundary."""

    def __init__(self, catalog: Any, data_root: Any) -> None:
        self.catalog = catalog
        self.data_root = data_root

    @staticmethod
    def execution_path(*, place_scope: bool, content_query: str | None) -> str:
        """Keep the three existing personal-search execution paths behind one decision."""
        if place_scope and content_query is None:
            return "place_album"
        if content_query is None:
            return "filter_only"
        return "semantic"

    def collect(self, original_query: str, *, today: date | None = None) -> dict[str, Any]:
        choices = place_choices.choices(
            self.catalog, text=original_query, data_root=self.data_root,
            include_parent_identity=True,
        )
        return parse_unified_intent(
            original_query, connection=self.catalog.connection,
            place_choices=choices, today=today,
        )

    def validate(
        self, plan: Any, *, original_query: str, parsed: dict[str, Any] | None = None,
        continuation: bool = False,
    ) -> dict[str, Any]:
        """Validate a client plan against server-parsed candidates and catalog identities."""
        value = dict(validate_plan_shape(plan, original_query=original_query))
        if parsed is None and not continuation:
            parsed = self.collect(original_query)
        scope = value.get("collection_scope")
        parsed_place_candidates: list[dict[str, Any]] | None = None
        if scope is not None and not isinstance(scope, dict):
            raise ValueError("搜索计划中的合集条件无效")
        if scope is not None:
            try:
                collection_id = int(scope["collection_id"])
                revision = int(scope["revision"])
                token = str(scope["instance_token"])
            except (KeyError, TypeError, ValueError, OverflowError) as error:
                raise ValueError("搜索计划中的合集条件无效") from error
            current = person_collections._require_collection_instance(
                self.catalog.connection, collection_id, token,
            )
            if int(current["revision"]) != revision or str(current["name"]) != str(scope.get("name", "")):
                raise ValueError("人物合集已变化，请重新搜索")
            value["collection_scope"] = {
                "collection_id": collection_id, "instance_token": token,
                "revision": revision, "name": str(current["name"]),
            }
            scope = value["collection_scope"]
        if parsed is not None:
            if parsed.get("state") == "unsupported":
                raise ValueError("这组条件暂不支持自动筛选，请改写或使用明确筛选。")
            if value.get("content_query") != parsed.get("content_query"):
                raise ValueError("搜索计划内容与原查询解析不一致，请重新搜索")

            candidates = parsed.get("candidates", [])
            by_type: dict[str, list[dict[str, Any]]] = {}
            for candidate in candidates:
                by_type.setdefault(str(candidate.get("type")), []).append(candidate)
            parsed_place_candidates = by_type.get("place", [])

            scope = value.get("collection_scope")
            if scope is not None and not any(
                str(scope.get("collection_id")) == str(item.get("collection_id"))
                and str(scope.get("instance_token")) == str(item.get("instance_token"))
                and str(scope.get("revision")) == str(item.get("revision"))
                and str(scope.get("name")) == str(item.get("name"))
                for item in by_type.get("collection", [])
            ):
                raise ValueError("搜索计划合集不属于原查询候选，请重新搜索")

            parsed_people = {int(item["person_id"]) for item in by_type.get("person", [])}
            if not set(value.get("person_ids", [])) <= parsed_people:
                raise ValueError("搜索计划人物不属于原查询候选，请重新搜索")

            place_kind, place_key = value.get("place_kind"), value.get("place_key")
            if place_kind is not None:
                selection = value.get("place_selection") or {}
                bound_keys = ("kind", "key", "source", "source_id", "selection_ref", "dataset_id", "dataset_version")
                if not any(
                    all(selection.get(key) == item.get(key) for key in bound_keys)
                    and str(item.get("kind")) == str(place_kind)
                    and str(item.get("key")) == str(place_key)
                    for item in by_type.get("place", [])
                ):
                    raise ValueError("搜索计划地点不属于原查询候选，请重新搜索")

            date_pair = (value.get("capture_from"), value.get("capture_to_exclusive"))
            if any(date_pair) and date_pair not in {
                (item["value"][0], item["value"][1]) for item in by_type.get("time", [])
            }:
                raise ValueError("搜索计划时间不属于原查询候选，请重新搜索")

            source_id = value.get("source_id")
            if source_id is not None and source_id not in {
                int(item["source_id"]) for item in by_type.get("source", [])
            }:
                raise ValueError("搜索计划来源不属于原查询候选，请重新搜索")

            person_match = value.get("person_match", "any")
            if person_match == "all" and len(value.get("person_ids", [])) < 2:
                raise ValueError("搜索计划人物匹配方式无效")
            if parsed.get("choice_kind") != "person_match" and person_match != parsed.get("person_match", "any"):
                raise ValueError("搜索计划人物匹配方式与原查询不一致，请重新搜索")
        source_id = value.get("source_id")
        if source_id is not None:
            if type(source_id) is not int or source_id < 1 or self.catalog.read_one(
                "SELECT source_id FROM source_roots WHERE source_id=?", (source_id,)
            ) is None:
                raise ValueError("图库来源已变化，请重新搜索")
        person_ids = value.get("person_ids", [])
        if person_ids:
            rows = self.catalog.read_all(
                "SELECT person_id FROM persons WHERE merged_into IS NULL AND person_id IN ("
                + ",".join("?" for _ in person_ids) + ")", tuple(person_ids),
            )
            if {int(row["person_id"]) for row in rows} != set(person_ids):
                raise ValueError("人物身份已变化，请重新搜索")
        place_kind, place_key = value.get("place_kind"), value.get("place_key")
        selection = value.get("place_selection")
        if (place_kind is None) != (place_key is None):
            raise ValueError("搜索计划中的地点条件无效")
        if place_kind is not None and selection is not None:
            if not isinstance(selection, dict) or selection.get("kind") != place_kind or str(selection.get("key")) != str(place_key):
                raise ValueError("搜索计划中的地点身份无效")
            current_places = parsed_place_candidates
            if parsed is None:
                current_places = place_choices.choices(self.catalog, data_root=self.data_root)
            current = next((item for item in current_places
                            if item.get("kind") == place_kind and str(item.get("key")) == str(place_key)), None)
            bound_keys = ("source", "source_id", "selection_ref", "dataset_id", "dataset_version")
            if current is None or any(selection.get(key) != current.get(key) for key in bound_keys):
                raise ValueError("地点目录已变化，请重新搜索")
        return value
