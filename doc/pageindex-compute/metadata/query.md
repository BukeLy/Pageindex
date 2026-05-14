# Metadata Query

## Filter DSL

Current filter syntax is a strict MongoDB/Chroma-style subset:

```json
{"source": "web"}
```

```json
{"$and": [{"year": {"$gte": 2020}}, {"type": {"$in": ["pdf", "md"]}}]}
```

Supported operators:

- `$eq`, including short form
- `$ne`, including missing-field matches
- `$in`
- `$gt`, `$gte`, `$lt`, `$lte`
- `$and`, `$or`

Constraints:

- filter depth <= 5
- each leaf has exactly one operator
- values are primitives only
- range operators reject boolean values

## SQL Translation

`build_metadata_filter_sql()` compiles the DSL into MySQL JSON expressions:

- equality uses `JSON_CONTAINS`
- range uses `JSON_EXTRACT`
- `$ne` uses `COALESCE(..., 0) = 0` so missing fields match
- paths and values are bound parameters

This is the correct security shape: LLM produces structured DSL; backend
validates and compiles.

## Sort

`sort` is a dict of metadata field to direction:

```json
{"year": -1, "score": 1}
```

The backend appends `id ASC` as a stable tiebreaker.

## Aggregates

Aggregate shape:

```json
{
  "n": {"$count": {}},
  "avg_score": {"$avg": "$score"},
  "latest_year": {"$max": "$year"}
}
```

Supported operators:

- `$count`
- `$sum`
- `$avg`
- `$max`
- `$min`

## Group By

`group_by` switches response shape to grouped rows:

```json
{
  "group_by": ["category"],
  "aggregates": {"n": {"$count": {}}}
}
```

Returns:

```json
{
  "groups": [{"category": "AHU", "n": 24}],
  "total": 1,
  "truncated": false
}
```

## Scale Concern

All of this currently runs against `FilePageIndex.metadata` JSON. For millions
of documents, the same DSL should compile to indexed typed columns or typed
metadata value tables instead of raw JSON scans.

