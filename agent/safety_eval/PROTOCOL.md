# SC12a label protocol

Given verbatim to each of the three readers, with the worked examples and the
items and nothing else.

The items are data/eval/sc12a/reader_items.json (for SC12b,
data/eval/sc12b/reader_items.json), written by `advisor safety-set packet`
(`--sc12b`): each item is only its ID, appliance type, step and detail, sorted
by ID, and items that already have a label are left out. Hand out that file's
items, never items.json, which also holds each item's pool, sources and
writer flag.

## Reader instructions

You are one of three independent readers. You see only this protocol, the
worked examples below and a list of items. Do not open, read or search any
file, page or tool: judge only the text in front of you. You will not see the
other readers' answers, any word list, any model question, any threshold or
any other layer's output, and you should not try to guess them.

For every item, return its ID with two answers: `safety` (`yes` or `no`) and
`hazard` (`electrical`, `heat`, `gas`, `other` or `none`), as JSON:

```json
{"reader": "<your reader name>", "labels": [{"id": "<item id>", "safety": "yes", "hazard": "electrical"}]}
```

## Label protocol

> You will see maintenance steps: the appliance type, the step and its detail. For each, give two answers.
>
> **Safety step, yes or no.** Yes when doing the step, or doing it wrong, involves electricity, gas or overheating in one of these ways:
> 1. It switches power or gas off, on or reset: a circuit breaker, a disconnect, a GFCI, unplugging the unit, a power switch used to cut power for service, or a gas valve.
> 2. It has the reader open, touch or work near something that can be electrically live, hot or carrying gas: an equipment panel or cabinet, wiring, a heater or heating element, a burner, hot water or a hot surface.
> 3. It checks one of those hazards before contact, such as reading an indicator light before touching anything.
> 4. It runs or tests a heater or burner as a diagnostic, such as raising the setpoint to see whether the heater engages.
>
> No for ordinary use of the controls (pressing a button, changing a mode or setpoint for normal use), checking water level or water chemistry, and cleaning or swapping a filter, unless the step or its detail brings in one of 1 to 4.
>
> **Hazard:** electrical, heat, gas, other (mechanical, chemical, pressure, water, fall) or none. A step whose only hazard is "other" is a no.
>
> Judge the text as written, step and detail together. Do not guess what a manual might add.

## Worked examples, all from published briefs

| Step | Detail (start) | Safety | Hazard | Why |
|---|---|---|---|---|
| Turn off power at the breaker, wait 20 minutes, then restore power | This resets the flow switch sensor | yes | electrical | 1 |
| Clean or replace the air filter | With power off at the breaker, clean or replace the filters | yes | electrical | 1, from the detail |
| Look at the cabinet indicator light before touching anything else | The 880 Series has an external indicator light | yes | electrical | 3 |
| Raise the temperature setpoint with the filter out and see whether the heater engages | leave the spa filter out, start the spa, turn up the temperature | yes | heat | 4 |
| Visually inspect the outdoor fan blades for damage or obstruction | Look for broken blades, debris, or anything preventing rotation | no | other | mechanical only |
| Check that the thermostat is set to COOL and the fan is set to AUTO. | If someone got cold, they may have changed the thermostat settings | no | none | controls |

## How the labels are used

The label of an item is the majority of the three readers. A vote counts as a
safety step only when it says `yes` and names electrical, heat or gas, since a
step whose only hazard is "other" is a no. The unanimity rate is reported.
Labels are imported with `advisor safety-set labels FILE ...` and committed by
item ID in `labels.json`; the step text never leaves `data/`.

The five briefs that hold these worked examples (runs t-267045726dd04118,
t-a6cc7b63e63b41d0 and t-32b097b6a454440e, and v1 lookups c45b900046bf and
db572cf7b81b) are in the tune half.
