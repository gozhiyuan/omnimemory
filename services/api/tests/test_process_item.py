import pytest

from app.tasks.process_item import process_item


def test_process_item_requires_item_id():
    with pytest.raises(ValueError):
        process_item.run({})
