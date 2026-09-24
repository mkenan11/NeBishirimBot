"""NeBishirim - Telegram sessiyalarinin Neon-da saxlanmasi."""

import copy
import json
import os

import psycopg


MAX_SESSION_BYTES = 512 * 1024


def _pack(value):
    """
    Telegram sessiyasindaki Python tiplerini
    JSON formatina cevirir.
    """

    if value is None or type(value) in (str, int, float, bool):
        return {
            "t": "value",
            "v": value,
        }

    if type(value) is list:
        return {
            "t": "list",
            "v": [_pack(item) for item in value],
        }

    if type(value) is tuple:
        return {
            "t": "tuple",
            "v": [_pack(item) for item in value],
        }

    if type(value) is set:
        return {
            "t": "set",
            "v": [_pack(item) for item in value],
        }

    if type(value) is frozenset:
        return {
            "t": "frozenset",
            "v": [_pack(item) for item in value],
        }

    if type(value) is dict:
        return {
            "t": "dict",
            "v": [
                [_pack(key), _pack(item)]
                for key, item in value.items()
            ],
        }

    raise TypeError(
        "Sessiyada desteklenmeyen tip: "
        + type(value).__name__
    )


def _unpack(node):
    """JSON formatindan Python tiplerini berpa edir."""

    if (
        not isinstance(node, dict)
        or set(node) != {"t", "v"}
    ):
        raise ValueError(
            "Sessiya formati yanlisdir."
        )

    kind = node["t"]
    value = node["v"]

    if kind == "value":
        if (
            value is None
            or type(value) in (str, int, float, bool)
        ):
            return value

        raise ValueError(
            "Sessiya deyer tipi yanlisdir."
        )

    if kind in (
        "list",
        "tuple",
        "set",
        "frozenset",
    ):
        if not isinstance(value, list):
            raise ValueError(
                "Sessiya kolleksiyasi yanlisdir."
            )

        items = [
            _unpack(item)
            for item in value
        ]

        if kind == "list":
            return items

        if kind == "tuple":
            return tuple(items)

        if kind == "set":
            return set(items)

        return frozenset(items)

    if kind == "dict":
        if not isinstance(value, list):
            raise ValueError(
                "Sessiya lugeti yanlisdir."
            )

        result = {}

        for pair in value:
            if (
                not isinstance(pair, list)
                or len(pair) != 2
            ):
                raise ValueError(
                    "Sessiya acar formati yanlisdir."
                )

            result[_unpack(pair[0])] = _unpack(pair[1])

        return result

    raise ValueError(
        "Taninmayan sessiya tipi."
    )


def _url():
    url = os.getenv("DATABASE_URL")

    if not url:
        raise RuntimeError(
            "DATABASE_URL tapilmadi."
        )

    return url


def load_session(user_id):
    """
    Istifadecinin sessiyasini Neon-dan oxuyur.
    Sessiya yoxdursa bos dict qaytarir.
    """

    with psycopg.connect(
        _url(),
        connect_timeout=10,
        prepare_threshold=None,
    ) as db:

        row = db.execute(
            """
            SELECT state
            FROM bot_sessions
            WHERE user_id = %s
            """,
            (int(user_id),),
        ).fetchone()

    if row is None:
        return {}

    state = _unpack(row[0])

    if type(state) is not dict:
        raise ValueError(
            "Sessiyanin kok hissesi dict deyil."
        )

    return state


def save_session(user_id, state, update_id=None):
    """
    Istifadecinin sessiyasini Neon-da saxlayir.
    """

    if type(state) is not dict:
        raise TypeError(
            "Sessiya dict olmalidir."
        )

    state = copy.deepcopy(state)
    def encode():
        return json.dumps(_pack(state), ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    payload = encode()
    if len(payload.encode("utf-8")) > MAX_SESSION_BYTES:
        recipe_state = state.get("recipe_state", {})
        active = recipe_state.get("active_detail", {})
        for mode, view in recipe_state.get("views", {}).items():
            details = view.get("details", {})
            active_key = active.get("cache_key", (active.get("page"), active.get("index")))
            view["details"] = ({active_key: details[active_key]}
                if active.get("mode") == mode and active_key in details else {})
        payload = encode()
    if len(payload.encode("utf-8")) > MAX_SESSION_BYTES:
        state.pop("recipe_state", None)
        state.pop("recipe_message_id", None)
        payload = encode()
    if len(payload.encode("utf-8")) > MAX_SESSION_BYTES:
        raise ValueError("Sessiya 512 KB limitini kecdi.")

    with psycopg.connect(
        _url(),
        connect_timeout=10,
        prepare_threshold=None,
    ) as db:

        db.execute(
            """
            INSERT INTO bot_sessions
                (user_id, state, updated_at)
            VALUES
                (%s, %s::jsonb, NOW())

            ON CONFLICT (user_id)
            DO UPDATE SET
                state = EXCLUDED.state,
                updated_at = NOW()
            """,
            (
                int(user_id),
                payload,
            ),
        )

        if update_id is not None:
            db.execute("""UPDATE processed_updates SET delivery_status = 'completed'
                WHERE update_id = %s AND delivery_status = 'processing'""", (update_id,))
    return state


def delete_session(user_id):
    """Istifadecinin sessiyasini silir."""

    with psycopg.connect(
        _url(),
        connect_timeout=10,
        prepare_threshold=None,
    ) as db:

        db.execute(
            """
            DELETE FROM bot_sessions
            WHERE user_id = %s
            """,
            (int(user_id),),
        )