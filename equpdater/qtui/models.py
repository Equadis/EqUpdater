"""A list model that updates by key.

``set_items`` takes the complete new list and changes only what differs:
rows that vanished are removed, new rows are inserted where they belong, and a
row whose content changed gets ``dataChanged`` for itself alone. So when one
addon or mod changes state, one delegate repaints; scroll position, hover and
selection of every other row are untouched. (A reset happens only through
``reset_items``, for a genuinely different list.)
"""

from __future__ import annotations

from PySide6.QtCore import QAbstractListModel, QByteArray, QModelIndex, Qt


class KeyedListModel(QAbstractListModel):
    def __init__(self, roles: list, key: str = "key"):
        super().__init__()
        self._roles = roles
        self._key = key
        self._items: list = []
        self.stats = {"inserted": 0, "removed": 0, "changed": 0, "resets": 0, "moved": 0}

    # -- Qt -------------------------------------------------------------
    def roleNames(self):
        return {Qt.ItemDataRole.UserRole + 1 + i: QByteArray(n.encode())
                for i, n in enumerate(self._roles)}

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._items)

    def data(self, index, role):
        if not index.isValid() or index.row() >= len(self._items):
            return None
        return self._items[index.row()].get(
            self._roles[role - Qt.ItemDataRole.UserRole - 1])

    # -- updating ---------------------------------------------------------
    def item_at(self, row: int) -> dict:
        return self._items[row]

    def row_of(self, key) -> int:
        for i, it in enumerate(self._items):
            if it[self._key] == key:
                return i
        return -1

    def reset_items(self, items: list):
        self.beginResetModel()
        self._items = list(items)
        self.endResetModel()
        self.stats["resets"] += 1

    def set_items(self, items: list):
        k = self._key
        new_keys = [it[k] for it in items]
        new_set = set(new_keys)
        # 1. drop rows that are gone (from the bottom, so rows above stay valid)
        for i in range(len(self._items) - 1, -1, -1):
            if self._items[i][k] not in new_set:
                self.beginRemoveRows(QModelIndex(), i, i)
                del self._items[i]
                self.endRemoveRows()
                self.stats["removed"] += 1
        # 2. insert new rows at their place, walking the new order
        have = {it[k] for it in self._items}
        for pos, it in enumerate(items):
            if it[k] in have:
                continue
            self.beginInsertRows(QModelIndex(), pos, pos)
            self._items.insert(pos, dict(it))
            self.endInsertRows()
            have.add(it[k])
            self.stats["inserted"] += 1
        # 3. a kept row that is out of order is *moved* (an addon that was
        # installed goes from AVAILABLE up to MANAGED): the delegate keeps its
        # identity, nothing is rebuilt, and the view keeps its position.
        cur = [it[k] for it in self._items]
        for pos, key in enumerate(new_keys):
            if cur[pos] == key:
                continue
            src = cur.index(key, pos)
            # moveRows semantics: destination is the row *before which* it lands
            self.beginMoveRows(QModelIndex(), src, src, QModelIndex(), pos)
            self._items.insert(pos, self._items.pop(src))
            cur.insert(pos, cur.pop(src))
            self.endMoveRows()
            self.stats["moved"] += 1
        # 4. same keys, same order: repaint only the rows that differ
        for i, it in enumerate(items):
            if self._items[i] != it:
                self._items[i] = dict(it)
                ix = self.index(i)
                self.dataChanged.emit(ix, ix)
                self.stats["changed"] += 1
