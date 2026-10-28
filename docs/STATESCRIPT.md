# Statescript on the wire (1.74)

What the client's readers do with the entity-channel records of type 1. Every statement here was
read out of the client (the chunk reader at 0x7FF7898A6450, the frame reader at 0x7FF78991E3C0) or
observed with a working live server; the encoders are in `ow174/game/statescript.py` and
`ow174/game/loadout.py`.

## The chunk

    w_u32 first | w_var span | bit fragmented | w_var size-in-bits | payload

`first 0` is a full frame: the client resets what it had and applies the payload as the whole
picture. A chunk whose last frame (first + span) is at or below the highest one already applied is
dropped - so an unchanged chunk may be sent again at any time, and a resend cannot double-apply.

## The frame

    bit lead(0) | bit H | [H: bit C | w_var CmFD | (in a delta) w_var LBSS] | instances | [entity vars] | per-instance data | event lists

An owner frame (H=1) is skipped unless its CmFD is larger than the last one applied - unsigned, so
CmFD 0 never applies. That skip is why a server that numbers its frames naively sees no error at
all: the frames simply stop having any effect.

**The CmFD rule.** The graph manager keeps a tick stamp (+0x900) set to the client's own tick when
the entity's graph-info frame arrives. An owner frame must exceed that stamp too, so a number that
only beats the previous CmFD can still be in the client's past. The working rule is

    CmFD = max(prev + 1, newest command frame + 8)

(frames below the tick leave the game "waiting for updates that never came": the client sits in a
wait loop and the screen freezes without a line of log). LBSS, the varint that follows CmFD in a
delta and in any frame wrapped at a non-zero base, is the body's bit length: it is what the client
jumps over when it already applied that frame.

A full frame carries everything: it first clears the network-set variables and destroys the
network instances it does not list. Send it as a delta (chunk at a non-zero base) only with LBSS
set; a chunk with data must arrive before an ack-only delta may cover its range.

## Scripting a fresh hero body: two records, in this order

1. The body's create record carries a graph-info frame: instance descriptors only, no variables,
   no states, CmFD 0. It only lifts the graph manager over the entity's create tick, and it leaves
   the manager at frame 2.
2. The activating full snapshot follows as its own module-1 record, chunked at base 0, its CmFD per
   the rule above. This frame creates the instances and sets the states.

Both deviations from this shape were observed live: an empty create chunk whose descriptor list is
populated as well creates every instance twice (the client reads a null graph info and dies at
null+0x1F8 once the activation wait times out), and a full snapshot wrapped at base 2 or higher is
ignored entirely - the first full-send count is what decides which frames create instances. The
snapshot is safe to resend (the client dedups by chunk range); after the pick of another hero the
body is a new entity under a new id (a destroyed id stays ignored for 30 s) and the pair starts
again for it.

The hero entity variable 0x03ac must carry the hero's asset: without it no StateHero node ever
starts; with a null guid the client exits on activation.

## State bits and their payloads

The per-instance list is one bit per networked state (graph's m_states minus the client-only and
server-only ones); a SubScript state that is on carries its child instance (w_u16) and a bit. For
the remaining classes a census over the metadata's 486 state types says:

| class | on-entry form |
|---|---|
| most states | the bare active bit |
| BooleanSwitch | active bit + one branch bit |
| Stack | active bit + two bits |
| GameMessageEntry | active bit + a compact u16 (its zero is one bit) |
| LogicalButton, WeaponVolley, STU_691BFA55 | a second bit precedes the entry, then the class payload |

A bare active bit for a class that has data shifts every later field in the frame: the client reads
the next instance's data as states, misfires, and either exits or freezes depending on what it
tried to run. Until the two-bit form is implemented, the three has-data classes cannot be sent.

## Hero select and the HUD

The controller's full frame mounts hero select (288A -> 288B) and, in the Practice Range, the HUD
(20E4). The HUD's subgraph state and its two begin-condition variables must be mounted together
with the hero's body scripts: mounted bare, the HUD opens an empty subgraph. The open and the
closed frame carry the same instance set - only the screen's presence bits change - or the client
keeps the post-pick presentation up and never lets the player into the world. Never write the
screen's initial-pick variable (0x0842): True kills the grid, False freezes the presentation, and
the client's own default does the right thing.
