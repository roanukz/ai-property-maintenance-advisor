"""SC12a and SC12b, offline: the labeled step set, its labels, the scorer and the lock.

Nothing here makes a paid call or imports agent.live; the live halves are
agent/live/sc12a.py and agent/live/sc12b.py, reached through `advisor eval`.

- pools.py builds pool G (every writer step in the published and live briefs
  and the recorded v1 cassettes) and pool D (sentences from full fetched
  pages, decision 53, at most config.SC12A_D_DOC_CAP per URL), dedupes
  them by step key, and splits them into tune and held out halves by
  document family (decision 58), balanced within each appliance (decision 59).
- labels.py imports the three readers' labels and takes the majority.
- stats.py holds the Wilson interval, recall and precision, the threshold
  selection, the choice rule and the reliability table.
- harness.py stores items, replies and the lock under data/eval/sc12a/, and
  scores the arms of success criterion 3.
- sc12b.py reads the SC12b inputs and takes a model's edges out of the graph
  for a first lookup.

Page text stays under data/: the committed files (item_ids.json, labels.json)
carry item IDs, hashes, halves and labels, never text.
"""
