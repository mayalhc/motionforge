# MotionForge

🇺🇸 English | [🇰🇷 한국어](./KO_index.md)

Type what a character should do. Get an animation in Blender.

MotionForge runs NVIDIA's ARDY motion model next to Blender and keys the result
onto a rig you can edit like any other. You can also steer the character live,
pin poses it has to hit, move the motion onto your own character, and hand it
straight to Cascadeur.

## What's new in 0.5.0

### New

**MotionBricks characters stand like people**
- A new MotionBricks character stands the way the idle style stands, arms down, instead of in the robot's zero pose.

**A converted style follows its clip**
- **Import FBX / BVH** fills in **Speed** with the speed the clip was performed at, and allows every generation length.
- Picking an armature already in the scene as **Source** fills in **Speed** the same way, so a dance done on the spot stays on the spot.
- A character walking the style plays the clip through in order, instead of circling its first few frames.
- Measured frame by frame against the source: a drunk walk comes out 13 cm from it instead of 18, and a taunt performed on the spot 8 cm instead of 15 - and it now stays on the spot.

**An FBX or BVH becomes a MotionBricks style**
- **Import FBX / BVH** in the MotionBricks panel brings a mocap or animation file into the scene and points the converter at it. **Convert to Style** moves the clip onto the skeleton MotionBricks walks and writes the file.
- No MotionBricks character has to exist first and none is left behind afterwards: a file goes in and a `.mbstyle` comes out, in the folder beside *walk* and the rest.
- **Rig Type** says what the file calls its bones - Mixamo, UE4, UE5 Mannequin, Fortnite, MetaHuman, Rigify, the metarig, TF2, SMPL-X and more - and **Auto-Detect** reads them, so the usual cases need no choice at all. Mixamo names without the `mixamorig:` in front, which is what CMU's converted takes and most BVH exports carry, are recognised too.
- A BVH's own frame rate is read from the file and the scene set to match, and whatever the rate the clip is re-timed to the runtime's 30 fps - a 120 fps take keeps its length instead of playing four times fast.
- **Style Name**, **Speed** and **Allowed Durations** are yours to change; Import fills in the last two from the clip. Everything the runtime tests is tested before the file is written, so a style it would refuse is refused here instead, with the reason, and nothing half-written is left in the folder.

**Styles from the scene as well**
- An animation that is already on a MotionBricks character can be saved out the same way with **Save as Style**.

### Changed

**A new style shows up at once**
- Converting or saving re-reads the styles folder. Before, the engine kept the list it loaded at startup, so a style replaced under the same name kept its old motion.

**UE4 joins the rig types**
- The UE4 and UE5 mannequins share one skeleton - UE5 only added IK bones to it - so both are in the list and both map the same way.

### Fixed

- A style converted from a Mixamo download no longer throws the character tens of metres through the floor.
- A style converted from a character with longer legs than the robot - a Fortnite dance - no longer hovers and bobs; its feet stay on the floor.
- The check after **Convert to Style** walks the style at its own Speed, so a clip performed on the spot is no longer reported as running away.

### Removed

- **Pull the Arms Back if Refused** - the clips it rescued convert properly now, with their arms.

---

## Setup, once

**1. Install the add-on.** In Blender: `Edit ▸ Preferences ▸ Add-ons ▸ Install`,
pick the `motionforge` folder, tick it on.

**2. Install the engines.** Still in the add-on's preferences, set the
**Engine Folder** to an empty folder on a drive with about 18 GB free, then
press **Install Engines**. MotionBricks, Kimodo and ARDY all go into that one
folder. **The download is about 18 GB, so it takes a long time** — anywhere
from several minutes to over an hour depending on your connection. A progress
bar in the same panel shows each file as it arrives, and Blender stays usable
meanwhile. If it stops partway, press **Install Engines** again: files already
downloaded are kept, and only the rest is fetched. When it is done, all three
engines show **ready**. MotionBricks needs Blender 5.2 or newer.

Press `N` in the 3D viewport and the **MotionForge** tab is there.

---

## The big picture, in 30 seconds

MotionForge is two pieces working together:

- **The engine** — NVIDIA's ARDY model. It turns a sentence into animation frames.
- **The add-on** — this one, running inside Blender. It builds a rig, plays the
  frames on it, lets you steer and pin poses, and copies the result onto your
  own character.

Every animation first lands on a **source rig** that MotionForge builds (the
**Create Rig** button). From there you can copy it onto any *other* armature —
called a **target rig** — so the motion plays on your own character.

---

## The seven panels

Press `N` in the 3D viewport and a **MotionForge** tab appears in the right-hand
sidebar. It holds seven panels, in the order you will use them.

### 1. MotionForge — connect and make a rig

| Control | What it does |
| --- | --- |
| **Skip Text Encoder** | Connects without reading prompts. Only for checking that ARDY starts. |
| **Connect ARDY** | Loads the engine. Ready in about a second. |
| **Create Rig** | Builds an armature — this is your **source rig**, the skeleton the model will animate. |
| **Generated Rigs** list | Every source rig you have made. **Use \<name\>** makes one the active source. |
| **Active source** | The rig that current takes are generated onto. |

### 2. Text to Motion — turn words into animation

| Control | What it does |
| --- | --- |
| **Takes** | Your shots. `+` adds one, `−` removes the selected. |
| — **Source Rig** | Which rig this take fills. Empty = the active source. |
| — **Prompt** | A plain sentence, e.g. *"a person walks forward"*. Start with "a person" — that is how the model was taught. |
| — **Duration** | Length in seconds. |
| — **Seed** | `-1` gives something new each time; a fixed number repeats the same result. |
| — **Continue** | Start from the end of the take *above*, for one continuous performance. |
| **Shared** | Options applying to every take: **Reduce Foot Skating** (cleaner feet and lands exactly on your waypoints/poses — leave it on), **Denoising Steps** (fewer = faster and rougher), **Prompt Strength**, **Constraint Strength**. |
| **Waypoints** | "Be here at this frame" markers that lay out a route. **Face Waypoint Direction** also steers which way the character looks. |
| **Pose Keys** | "Look like this at this frame". Each gets its own editable copy of the rig. |
| **Generate Take / Generate All** | Make the selected take, or every ticked take. |

### 3. Live Path — steer while it plays

| Control | What it does |
| --- | --- |
| **Live takes list** | One row per character in the run. **+** adds a row; each carries its own prompt, rig, seed and target. |
| **Target** | The cone that character walks toward. The **+** beside it makes one. A row with no target just moves as its prompt says. |
| **Start Live / Stop** | Begins or ends live steering, for every row at once. `Esc` also stops it. |
| **Stop After** | Ends the run automatically after this many seconds. `0` runs until you stop it. |
| **Face Target Direction** | Powers the cone's rotation so the character can walk sideways, backwards, or round something while watching it. |
| **Bake / Discard** | Writes the held take as a real Action, or throws it away. |

### 4. Kimodo — text to motion, on your own machine

| Control | What it does |
| --- | --- |
| **Character list** | Every Kimodo character in the scene, one per row. The **tick box** decides who takes part; the highlighted row is the one you are editing. Each row shows **77** (with fingers) or **30** (without). |
| **+** / **−** | Adds a character at the 3D cursor, or takes the selected one out of the list. **−** asks whether to delete the armature too. |
| **Eyedropper** | Adds the selected armature to the list, if it has Kimodo's bones. |
| **With a Body** | A new character gets a body mesh. |
| **With Fingers** | A new character gets hands with fingers. Untick it for a simpler skeleton without them. |

Each character carries its own settings, shown under the list:

| Per character | What it does |
| --- | --- |
| **Prompt** | A plain sentence, e.g. *"a person walks forward"*. |
| **Seed** | `-1` gives something new each time; a fixed number repeats the same result. |

| Shared | What it does |
| --- | --- |
| **Duration** | Length of each take in seconds. |
| **Denoising Steps** | Fewer is faster and rougher. |
| **Prompt Strength** | How closely it follows the prompt. Higher is more literal and less natural. |
| **Constraint Strength** | Leave it as it is. |
| **Reduce Foot Skating** | Stops a foot sliding while it stands on the ground. On by default. |
| **Generate Motion** | Makes a take for every ticked character, one after another, and keys each as soon as it is ready. |
| **Status line** | Which character is being made, how long it has been running, and whether it is on the GPU or the CPU. |
| **Export SOMA BVH** | Saves the selected character's animation for NVIDIA's SOMA Retargeter, which turns it into humanoid robot motion. |
| **Reload Engine** | Unloads Kimodo. The next take loads it again. |

### 5. MotionBricks — walk without typing

| Control | What it does |
| --- | --- |
| **Character list** | Every MotionBricks character in the scene, one per row. The **tick box** decides who takes part; the highlighted row is the one you are editing. |
| **+** / **−** | Adds a character at the 3D cursor, or takes the selected one out of the list. |
| **Eyedropper** | Adds the selected armature to the list, if it is a MotionBricks character. |
| **With a Body** | A new character gets a simple body, weighted to the skeleton. Bones alone are hard to follow while a character walks around. Untick it for bones only. |

Each character carries its own settings, shown under the list:

| Per character | What it does |
| --- | --- |
| **Style** | How this one moves. Walking, slow, stealthy, scared, zombie, boxing, crawling on hands or elbows, happy dancing, limping, and walking with a gun. Also shown on its row, so a crowd reads at a glance. |
| **Go** | **In a Direction** keeps going the way you set — and follows the 3D cursor while live. **To a Target** walks to an object of its own and stops, cursor or not. |
| **Heading** | Which way this one walks, when you chose a direction. |
| **Target** | The object this one walks to. The **+** beside it makes one. Turn the object and the character faces that way on arrival. |
| **Speed** | Metres per second. Leave it at zero to use the style's own pace. A style with no pace of its own, like *Idle*, stays where it is. |
| **Seed** | Changes where the stride begins. If a walk lands awkwardly, try another number. |

| Shared | What it does |
| --- | --- |
| **Duration** | Seconds to make, for every ticked character. |
| **Reduce Foot Skating** | Stops a foot sliding while it stands on the ground. On by default. |
| **Continue** | Carry on from where the last one stopped, in the same animation. |
| **Generate Motion** | Makes it, for every ticked character at once. They start walking straight away — no keyframes are written yet. |
| **Bake** | Writes what you are watching as keyframes, for all of them. |
| **Discard** | Throws it away instead. |
| **Steer With** | How you drive them live: **3D Cursor** — shift-right-click anywhere and they walk there, facing the way they go — or **Target Object**, where each walks to its own. |
| **Move Characters Too** | Your own characters walk along with them, using the rows in **Share With Characters** below. They are posed, not keyed. |
| **Lead** | How many frames to keep planned ahead. Higher rides out a slow frame; lower answers the controls sooner. |
| **Start Live** | Walks now, steered as it goes. `Esc` stops it. |

### 6. Retarget — put the motion on your own character

| Control | What it does |
| --- | --- |
| **Source** | The rig carrying the motion. Usually left as the generated rig. |
| **Target** | *Your* character's armature. |
| **Rig Type** | The naming convention of the target (UE5, Fortnite, Mixamo, Rigify, SMPL-X…). **Auto-Detect** usually gets it right. |
| **Auto-Align Rest Pose** | Swings the target's limbs onto the source's rest pose first, so an A-pose character does not look broken. |
| **Auto Scale Motion** | Scales how far the body travels to fit the target's height. |
| **Match Floor Contact** | Lifts or lowers the target each frame so its feet meet the floor. |
| **Retarget** | Runs the copy. |
| **Share With Characters** | Copies each take onto several characters at once (see below). |

### 7. Send to Cascadeur — hand the motion over

Always visible; it tells you what it needs rather than hiding until the right
thing is selected.

| Control | What it does |
| --- | --- |
| **Live: Mirror This Rig** | Pose here or in Cascadeur and the other side follows, in real time. See [*Watching it in Cascadeur, live*](#watching-it-in-cascadeur-live). |
| **Send** | **Character and Bones** sends the rig itself — bones, meshes, animation — as a scene Cascadeur opens. **Keyframes Only** puts motion on a character Cascadeur already has. |
| **Rig Type** | Keyframes mode only. Which naming your rig uses, so its bones can be matched to Cascadeur's. Auto-detected; set it by hand if the guess is wrong. |
| **With Mesh** | Character mode only. Include the meshes bound to the rig. |
| **Unify Skeleton** | Character mode only. Sends one skeleton instead of the rig's internal parts, so hats, pouches and weapons arrive attached. Leave it on; your rig is not modified. |
| **Fill Rig Mode** | Fills Cascadeur's Quick Rigging Tool with this rig's joints, so Rig Mode does not have to guess them from their names. |
| **Send Frame Range** | Sends the scene's frame range in one go. |
| **Current Frame** | Sends just the pose you are looking at. |
| **ⓘ** | Asks Cascadeur how many frames it has received. |

See [*Sending it to Cascadeur*](#sending-it-to-cascadeur) for the order these
have to be used in.

---

## Kimodo

A third way to make motion. Like **Text to Motion**, you type what the
character should do — but Kimodo runs by itself, with no install console and
nothing to connect first.

**Make a take**

1. Open the **Kimodo** panel.
2. Press **+**. A character appears at the 3D cursor.
3. Type a **Prompt** under the list — start with *"a person walks forward"*.
4. Set **Duration** and press **Generate Motion**.

A take takes a while — longer on the CPU than on the GPU. Blender stays usable
while it works, and the status line counts the seconds. When it finishes, the
motion is already keyed on the character; press play. `Esc` stops waiting, but
the take that is running cannot be interrupted.

Did not press **+** first? **Generate Motion** makes a character for you.

**Several characters**

Press **+** for each one and give each row its own **Prompt** and **Seed**.
**Generate Motion** makes them one after another, and each character gets its
motion as soon as its own take is done. Untick a row and that character sits
out.

**Send it to a humanoid robot**

**Export SOMA BVH** saves the character's animation as a BVH file that
NVIDIA's [SOMA Retargeter](https://github.com/NVIDIA/soma-retargeter) reads.
That tool turns it into joint animation for Unitree G1 and H2, Booster T1 and
the AgiBot X2 and A3, and writes a CSV their control and simulation tools take.

The file holds whatever the character does now, so edits you made in Blender
travel with it. Install the retargeter separately - it is a project of its own
and nothing here is needed for it to run.

**Put it on your character**

The **Retarget** panel works exactly as it does for *Text to Motion*. The
character you added last is already set as the source.

**Send it to Cascadeur**

In **Send to Cascadeur**, choose **Character and Bones**, leave **Fill Rig
Mode** on and press **Send Frame Range**. The character opens in Cascadeur with
the Quick Rigging Tool filled in, fingers included.

**Wait before opening the Quick Rigging Tool.** The joints are filled in about
30–40 seconds after the character appears in Cascadeur. The Send panel shows
`Quick Rig: waiting for Cascadeur to settle` until then, and
`Quick Rig: … registered as SOMA (Kimodo)` when it is done. Opened too early, the
tool shows legs, neck and spine only partly filled.

**Live: Mirror This Rig** works too, both ways, timeline included.

## MotionBricks

The other way to move a character. **Text to Motion** takes a sentence;
this takes a style and a direction, and it is quick enough that trying five
of them costs nothing.

**Make a walk**

1. Open the **MotionBricks** panel.
2. Pick a **Style** — start with *Walk*.
3. Set **Duration** and press **Generate Motion**.
4. Press play. If you like it, press **Bake**.

A character appears if you did not have one — bones with a body over them —
and it walks immediately —
**Generate Motion** does not write keyframes, so trying ten walks costs
almost nothing. **Bake** turns the one you kept into keyframes; **Discard**
throws it away. Nothing else in Blender sees the motion until you bake it,
so retargeting and exporting come after that.

**Send it somewhere**

1. Set **Go** to *To a Target* and press **+**.
2. Move the marker where the character should end up.
3. Turn the marker so its arrow points the way the character should face when
   it gets there.
4. **Generate Motion**, then **Bake**.

It walks over, slows down as it arrives, turns to face the arrow and stands
still. Give it more seconds than it needs — waiting there costs nothing, and
too few seconds stops it partway.

**Keep going**

Tick **Continue** and press **Generate Motion** again. It adds to whatever is
there, baked or not, so you can build up a long performance a few seconds at a
time and bake the whole thing once at the end. Change the style or the
target first and the character changes what it is doing on the way, with no
join to hide.

**A crowd**

Press **+** for each one and give each row its own **Style**. They all walk at
once, live or generated: one walking, one idling, one crawling, each at its own
speed.

Untick a row and that character sits out — it keeps what it already has and is
simply not part of the next **Generate Motion** or **Start Live**. Tick it back
on whenever you want it moving again.

While live, a character set to **In a Direction** follows the 3D cursor, and one
set to **To a Target** walks to its own object instead — so you can send most of
a crowd somewhere with one click while a few keep their own errands. A character
whose style has no pace of its own, like *Idle*, stays where it is rather than
being dragged along.

Deleting a character from the scene drops its row too.

**Steer it as it walks**

**Generate Motion** asks for a stretch of movement all at once. **Start Live**
does it the other way: the character walks immediately and keeps walking while
you steer it.

1. Under **Live**, leave **Steer With** on **3D Cursor**.
2. Press **Start Live**.
3. Shift-right-click anywhere in the viewport. The character walks there,
   facing the way it is going, and waits when it arrives. Click somewhere
   else and it sets off again.

   Only where you clicked on the floor matters. Click a wall or a prop and
   the cursor still sits on the ground, so you can always see the spot you
   picked. It stays on whatever height the cursor was at when you pressed
   **Start Live**, and goes back to behaving normally when you stop.
4. `Esc` stops it. Press **Bake** to keep what it did.

The cursor is the quickest way to direct it: nothing to select, nothing to
drag, and it goes wherever you point. **Target Object** is the other way, and
worth it when you care which way the character ends up looking — it faces the
object's arrow instead of the way it walked in.

It adds to the same held motion **Generate Motion** uses, so the two can be
mixed in one take — walk a stretch live, then ask for ten more seconds to a
target, then bake the lot at once.

**Your own character, while it walks**

Leave **Move Characters Too** on and the characters listed in **Share With
Characters** walk along with it as you steer, rather than only after you bake.
They are posed, not keyed, so there is nothing of theirs to undo — the walk is
still written by **Bake** and **Retarget**.

This is also the answer to the head. A MotionBricks skeleton has no neck or
head, but your character does, and retargeting only touches the bones the two
share — so the head stays where your rig puts it instead of being dragged
around by a robot that has none.

**Put it on your character**

The **Retarget** panel below works exactly as it does for *Text to Motion* —
the walk moves onto your own character, scaled to their size, feet on the
floor. The head and neck stay where they are: MotionBricks moves the body and
the arms, and has nothing to say about the head.

**Make a style from a file**

A style is a short clip MotionBricks is shown before it is asked for anything,
and it can come from any animation you have:

1. Press **Import FBX / BVH** and pick the file - a CMU take, a Mixamo
   download, an engine's export, anything with a skeleton in it.
2. **Source** fills in and **Rig Type** is read from the bone names. If it
   says *unrecognised*, pick the type by hand: Mixamo, UE4, UE5 Mannequin,
   Fortnite, MetaHuman, Rigify, the metarig, TF2 or SMPL-X.
3. Set **Style Name**, **Speed** - the walking speed the style is meant to be
   used at - and **Allowed Durations**, how many of the planner's generation
   lengths may use it. Import fills both in (picking a Source fills Speed): the clip's own speed, and all 11,
   which is what follows the clip most closely.
4. Press **Convert to Style**. The file lands in the styles folder and is in
   the **Style** list straight away.

The clip is re-timed to the runtime's 30 fps on the way, so its length in
seconds is what it was in the file. Nothing has to be in the scene first, and
what you imported stays where it is.

**Save your own style**

A style can also be made from what a MotionBricks character is already doing -
a generated take, a retargeted walk, your own keys. **Save as Style** in the
same panel writes the active character's animation out exactly the same way.

## Source rig vs. target rig, plainly

- **Source rig** = the armature MotionForge *makes* and animates. You never have
  to rig it; the model already knows its skeleton.
- **Target rig** = any other armature you want the motion on. It can be an
  Unreal Engine mannequin, a Mixamo character, a Rigify rig, an SMPL-X body —
  anything with reasonably named bones that matches one of the presets.

The flow is always the same: **generate on a source → retarget onto a target.**

---

## A beginner's recipe, end to end

1. **Install and connect** (see *Setup, once* above). Wait for **Ready**.
2. **Create Rig** — press it. An armature appears.
3. Add a take with `+`, type what you want, press **Generate Take**.
4. Scrub the timeline to watch it. The rig moves.
5. To put it on your own character: open **Retarget**, pick your character as
   **Target**, choose its **Rig Type**, press **Retarget**.
6. To steer or pin specific moments, add **waypoints** or **pose keys** and
   generate again.

That is the whole loop. Everything else in this guide makes those six steps
better — longer performances, live steering, and copying to many characters.

---

## Words you'll see (glossary)

| Term | Meaning |
| --- | --- |
| **Action** | Blender's name for an animation clip. Each take gets its own Action. |
| **Take** | One shot: its own prompt, its own length, its own Action. |
| **Source rig** | The MotionForge-built armature that holds the generated animation. |
| **Target rig** | Your character's armature that receives the motion. |
| **Retarget** | Copying animation from the source rig onto the target rig. |
| **Waypoint** | "Be at this spot on this frame." |
| **Pose key** | "Look like this on this frame." |
| **Foot skating** | A foot sliding along the ground when it should stay planted. |

---

## Your first animation

**Connect ARDY.** The first time, this downloads several GB. Wait for *Ready*.

**Create Rig.** An armature appears — this is the skeleton the model animates.

**Press `+`, write what should happen, press Generate Take.**

```
a person walks forward
a person sits down on a chair
a person waves with the right hand
a person jumps over something
```

Plain sentences work best. "a person" is how the model was taught, so start
there. A few seconds later the rig is animated and you can scrub the timeline.

---

## Takes

A take is one shot: its own prompt, its own length, its own Action.

```
☑ a person walks forward       100f ▶
☑ a person sits down            60f ▶
☐ a person waves                 ○
```

- `+` and `−` add and remove takes
- the checkbox decides whether **Generate All** includes it
- `▶` puts that take's animation back on the rig
- `○` means it has not been generated yet

**Generate Take** does the selected one. **Generate All** does every ticked one.

### Chaining takes

Tick the 🔗 on a take and it starts exactly where the take above ended — same
spot, same facing, same pose. Chain several and **Generate All** gives you one
continuous performance built out of separate instructions.

Take 1 has nothing before it, so it cannot be chained.

---

## Steering it

### Waypoints — go through here at this time

Move the 3D cursor, press `+`, set the frame. The character will be at that
spot on that frame. Add more for a route.

Turn on **Face Waypoint Direction** and the empty's green arrow also decides
which way the character looks when it gets there.

### Pose keys — look like this on this frame

Go to the frame you want, press `+`, then pose.

Each key gets **its own rig** — a copy of the generated one named `cskel27_001`,
`cskel27_002`, and so on — and `+` drops you straight into pose mode on it.
Pose that copy, not the animated rig. It carries no animation of its own, so
what you pose stays put no matter how much you scrub.

Add as many as you like: frame 30, frame 60, frame 100. Press **Generate** and
the take is made to pass through all of them in order — A to B to C.

- **Whole Body** holds every joint. That frame comes out looking like what you
  posed.
- **Parts** holds only the hands and feet you tick, and lets the model invent
  the rest of the body around them.

Mix them freely: pin the first frame exactly, and only a hand later on.

**IK On** puts hand and foot handles on the selected key's rig so you can pose
it by dragging instead of rotating 27 bones:

- Blue cubes on the hands and feet — drag one and the limb follows.
- Yellow pyramids at the elbows and knees — these are pole targets. Move one to
  say which way the joint bends.
- **IK Off** bakes what IK is showing onto the bones and removes the handles.
  The pose stays exactly as it looked.

Turning IK on never moves the pose: the handles are built where the limbs
already stand, and each pole angle is measured from the bend the pose already
has. If a limb jumps when IK comes on, that is a bug, not you.

The poses are read when you press Generate, not when you press `+`. So adjust
a pose and Generate again — no need to re-add the key. **Pose \<name\>** in the
panel jumps back to a key's frame and selects its rig for editing; removing a
key deletes its rig with it.

Moving a pose rig moves where the character will be on that frame, since the
root position is part of the pose. Leave it where it is unless you mean that.

> **Keep "Reduce Foot Skating" on.** It is also the step that lands the motion
> exactly on your waypoints and pose keys. With it off they become suggestions.

---

## Live Path — steer while it plays

This is the fun one.

1. Open **Live Path** and press **+**. A row appears — give it a prompt and
   pick the rig it drives.
2. Press **+** beside **Target**. A cone appears — its point is the direction
   the character will face.
3. Press **Start Live**. The character begins moving.
4. **Drag the cone.** The character walks toward it.

Press `Esc` or **Stop** to end it. Nothing is keyed until you **Bake**.

**Animate the cone instead of dragging it.** Keyframe it, parent it to a
curve, drive it however you like — MotionForge just reads where it is each
time it needs more motion. That gives you a repeatable path you can re-run with
different prompts or seeds, and exact control over timing.

Tick **Face Target Direction** and the cone's rotation steers facing too, so
the character can walk backwards, sidestep, or circle something while watching
it.

**Several characters at once.** Add a row per character and press **Start
Live** once. Each walks to its own cone, on its own prompt. They can stand
anywhere and face any direction — a target is read from where that rig stands,
not in world coordinates.

**Stop After** ends the run on its own after a set number of seconds. Leave it
at `0` and it runs until you stop it; there is no hidden limit, and the room
ahead grows as the take does.

**What to expect**

- Steering shows up about 2 seconds later. What you are watching was already
  decided.
- Keep the cone roughly a walk ahead of the character. Put it too far and the
  character breaks into a run; jerk it around and the footwork gets messy.
- Live skips the foot-skating pass. If a live path is exactly what you wanted,
  rebuild it with waypoints for the cleaner result.

**If it cannot keep up**, the panel says where the time goes:

```
12.4s generated, 1.8s ahead
gen 130 + hold 0 of 2000 ms
```

The last number is how long one window plays for. As long as the first two add
up to less than it, the loop is keeping ahead.

- **gen** is generation. On an RTX 5080 one character costs about 130 ms and
  four cost about 490 ms, because the characters are generated one after
  another.
- **hold** is what it costs to keep the window in memory, which is nothing.

MotionForge generates further ahead when a cycle takes longer, so adding
characters does not make it stutter. It only asks for more room when it needs
it, because running further ahead makes dragging the cone slower to show up.

### Bake

Live never writes keys as it runs. Keying redraws the viewport for every frame
of every window - 658 ms of a 2000 ms budget in a real editor - so the take is
held in memory instead and the loop stays clear.

When you stop, the panel says how many seconds are waiting and a **Bake**
button writes the whole take in one go. It is there whenever something is
waiting, so you can generate as long as you like and decide afterwards; the
rig does not move until you press it. **Discard** throws the take away.

---

## Roadmap: SMPL-X bodies

MotionForge makes motion but has no body. [SMPL-X][smplx] is the opposite — a
parametric human body (10 shape numbers, 55 joints) with the deformation
research behind it. Meshcapade's [Blender add-on][mc] builds one in Blender,
and Perceiving Systems' [Unreal plugin][ps] computes its pose-corrective blend
shapes *at runtime* instead of baking them per frame. That last part is what
makes it a fit: it means generated and streamed motion works as well as
recorded motion, with no bake step.

Four steps, in the order they are being built:

| # | Step | State |
| --- | --- | --- |
| 1 | **Retarget onto SMPL-X / SMPL-H** — pick the body in the Retarget panel like any other rig | **done** |
| 2 | **Export `.smpl` / `.npz`** — the pose parameters themselves, for AMASS, BEDLAM and Meshcapade tooling | **done** |
| 3 | **Live Link to Unreal** — stream a take into UE while it generates, correctives computed live | **stream verified in UE** |
| 4 | **Shape-aware retargeting** — the motion is corrected for the body's build | **done** |

The order is deliberate. Step 1 reuses the retarget engine that already works
on UE5, Fortnite, Mixamo and Rigify, so it is the least new machinery for the
most result, and it is the thing the other three stand on. Step 2 is a small
writer once step 1 defines the joint mapping. Step 3 is the one worth having —
steering a photoreal character in Unreal from a text prompt, with nothing
baked — but it needs 1 and 2 first.

### Step 3: the motion stream

Turn on **Stream to Unreal** in Live Path and each window is pushed over TCP
as it is generated, to anything listening on the port. One JSON object per
line, so a receiver is a socket and `json.loads`:

```
{"type":"hello","protocol":1,"skeleton":"cskel27",
 "joints":["Hips",...],"parents":[null,0,...],
 "rest":[[x,y,z],...],"fps":20.0,"up":"y"}

{"type":"frame","index":0,"time":0.0,
 "translation":[x,y,z],"pose":[[rx,ry,rz],...]}
```

`pose` is one axis-angle rotation per joint relative to the rest pose, in the
order `joints` gives — enough to run forward kinematics. Y is up. A late
joiner gets the hello and then whatever comes next; a listener that leaves is
dropped without disturbing the generation.

Verified end to end: 120 frames of a live take, in order, and running the
kinematics from the stream puts every joint within 2 µm of where the rig is
standing in Blender.

### The Unreal plugin

`MotionForgeLiveLink` lives in the MetaHuman_FAB58 project's `Plugins` folder.
It adds **MotionForge** to Live Link's *Add Source* list: give it
`127.0.0.1:9560`, and the take appears as an animation subject while Blender
is still generating it.

It converts on the way in — metres to centimetres, Y-up right-handed to Z-up
left-handed — and publishes bones parent-relative, which is what Live Link
expects. It reconnects on its own, so the order you start Blender and Unreal in
does not matter.

From there: a **Live Link Pose** node in an Anim Blueprint, then the SMPL
plugin's **SMPL Pose Correctives** node after it. The correctives are computed
from whatever pose arrives, so a generated take deforms the body exactly as a
recorded one would.

The stream carries ARDY's own joint names (`Hips`, `Spine`, `LeftUpLeg`…), not
SMPL-X's. Use a Live Link Remap Asset if your target skeleton names differ.

**Verified in Unreal**, on UE 5.8:

```
LogMotionForge: Subject published: skeleton 'cskel27', 27 joints, 20.0 fps.
LogMotionForge: First frame pushed to Live Link.
```

A subject reading *Subject Invalid* before the first frame is normal - the
skeleton arrives first and the subject only becomes valid once frames follow.
It goes back to invalid when a take ends, for the same reason.

#### The skeleton to receive on

Live Link does not create anything in Unreal - it drives a Skeletal Mesh that
is already there. So you need a skeleton, and its bones have to match what the
stream sends.

**Export Rig for Unreal (FBX)**, next to the stream settings, writes one built
for the job. Import it in Unreal and it lines up with no remapping at all.

It is not the rig you see in Blender. Live Link writes each streamed transform
straight into a bone's local slot, so the receiving skeleton has to share
ARDY's convention: every joint's rest orientation is the identity. The Blender
rig aims each bone at its child to be readable, which is up to 128 degrees away
from that - every rotation would land twisted by the difference. The exported
one has every bone pointing the same way. It looks like a bundle of sticks, and
that is the point: it is a driver.

From there, Unreal's **IK Retargeter** moves the motion onto the character you
actually want - a MetaHuman, an SMPL-X body - which is the same route BEDLAM
uses.

#### Wiring it up

1. Import the FBX. Unreal makes a Skeletal Mesh and a Skeleton.
2. Right-click the Skeleton, Create Animation Blueprint.
3. In the AnimGraph: **Live Link Pose** (Subject: `MotionForge`) into
   **Output Pose**. Add the SMPL plugin's **SMPL Pose Correctives** node
   between them when you are driving an SMPL body.
4. Drop the mesh in the level and set its Anim Class to that Blueprint.

**Nothing has watched a character move yet.** The stream reaches Live Link and
the skeleton is built to match it, but how the motion looks in Unreal is not
confirmed.

### Step 4: standing on the same floor

ARDY generates on one fixed skeleton. Retargeting already scaled the stride to
the target's legs, but nothing put its feet on the ground, so a body built
taller or shorter hovered above the floor or sank into it.

**Match Floor Contact** in the Retarget panel fixes that. Each frame, the
target is raised or lowered so its lowest joint sits where the source's does,
scaled to its own build. A planted foot lands on the floor; a jump is still a
jump.

Measured on bodies built at 0.7x and 1.4x: a planted foot went from 45 mm off
the floor to 0.0 mm, with nothing sinking through, and the take kept both its
travel and its rise and fall.

True shape-*conditioned generation* — the body's build changing how the model
moves in the first place — would need ARDY retrained with shape as an input,
which is not something this add-on can do. What is achievable, and what this
is, is making the motion correct **for** the body it lands on.

**What you need.** SMPL-X model files are licensed separately: free for
academic use from the [project page][smplx], commercial through
[Meshcapade][mc-lic]. They cannot ship with this add-on.

**What will not work yet.** SMPL-X wants 30 finger joints. The released ARDY
model has one thumb joint per hand, so hands stay in their neutral pose. The
77-joint SOMA skeleton has full fingers, but NVIDIA has not published it.

[smplx]: https://smpl-x.is.tue.mpg.de/
[mc]: https://github.com/Meshcapade/SMPL_blender_addon
[mc-lic]: https://meshcapade.com/
[ps]: https://github.com/PerceivingSystems/smpl-unreal

---

## One take, several characters

Set the characters up **before** you generate, and every take lands on all of
them at once.

In the retarget panel, under **Share With Characters**: select a character,
press `+`, pick its rig type if Auto guesses wrong. Add two or three. From then
on, Generate — and Bake, after a live take — copies the motion onto every one.

- **Fill Characters After Generating** — untick to stop it happening
  automatically.
- **Fill Characters** — do it now, for a take that is already made.
- The tick box beside each character skips it without removing it.

**Why not during a live take?** Because it does not fit. A retarget costs about
55 ms a frame per character, measured: one character needs 2197 ms for a
40-frame window, two 4598 ms, three 6787 ms — and a live window has 2000 ms to
be ready in. So the copy happens once a take exists, which is also when it can
be done in one pass instead of forty.

---

## Moving it onto your own character

Open **Retarget**, pick your armature under Target, choose its family
(UE5, Fortnite, MetaHuman, Mixamo, Rigify, TF2 Trifecta, SMPL-X…) and press
Retarget. **Auto-Detect** usually gets it right on its own.

Verified on Unreal Engine 5 mannequins, Fortnite exports, Mixamo rigs, Rigify
and SMPL-X bodies, and on the Scout, Soldier, Heavy, Medic, Sniper and Spy in
both their Legacy and New Trifecta rigs — including rigs built at centimetre
scale.

**SMPL-X / SMPL-H.** Build a body with Meshcapade's Blender add-on, pick it as
the Target, and generate. Fingers stay in their neutral pose — the released
ARDY model has one thumb joint per hand and nothing else.

Once the motion is on an SMPL body, **Export SMPL (.smpl)** appears under the
Retarget button. That file is the pose parameters themselves rather than a
baked skeleton, so it opens in AMASS, BEDLAM and Meshcapade's tools, and it is
what Perceiving Systems' Unreal plugin wants. Body shape is read from the
body's own shape keys if the Meshcapade add-on left them there.

---

## Watching it in Cascadeur, live

The Send buttons hand over a finished take. **Live: Mirror This Rig**, on the
Send to Cascadeur panel, is earlier than that: pose the rig in Blender and
the character in Cascadeur moves with it, in real time, while you work.
Nothing is installed for it - no plug-in, no Administrator.

**In Cascadeur, run Receive Poses (Blender) first** - commands menu. That is
what the whole panel needs, Live included; nothing here works before it is
running. **Then send the character across once, and press Live.** Live
reuses the same bone mapping and reference pose Send does, so a character
that has not been sent yet has nothing for Live to drive.

**Which way it mirrors** is a dropdown next to the button:

- **Follow the Focus** - pose in whichever window you are working in, and
  the other one follows. Click over to Cascadeur and it takes over from
  there.
- **Blender to Cascadeur** - only what you do here is sent.
- **Cascadeur to Blender** - only what you do there comes back.

**How much of the rig moves** is the dropdown beside it: the mapped bones
Send always uses, or the whole skeleton (only when the character in
Cascadeur is the one this rig sent).

Playing an animation in Blender plays it in Cascadeur too, keyed there as it
goes. **Stop Mirroring** ends the session; whatever already arrived in
Cascadeur stays.

## Sending it to Cascadeur

Motion made here can go straight onto a character in Cascadeur. No FBX to
export by hand, no import dialog.

**Turn Cascadeur's side on first.** In Cascadeur: `Animation Scripts` →
**Receive Poses (Blender)**. It starts listening on `127.0.0.1:9564`; running
it again stops it. This needs the Cascadeur plug-in installed.

### The order matters

1. **Send Character** — sends this rig, its meshes and its animation. Cascadeur
   opens it in a tab of its own.
2. **Send Frame Range** (Keyframes Only) — sends new motion onto the character
   that is now there.

Do it the other way round and the keyframes have nothing to land on.

**Send Character again after restarting Cascadeur.** When the character
arrives, Cascadeur records the orientation every joint came in at, and the
keyframes are measured against that record. Restarting Cascadeur loses it, and
keyframes sent afterwards will be turned the wrong way — quietly, with nothing
reporting an error.

### Sending onto Cascadeur's own character

You do not have to send a character first. If Cascadeur already has one of its
own open, switch to **Keyframes Only** and send: bones are matched by role, so
a UE5, Mixamo, Rigify or SMPL-X rig is translated into Cascadeur's own joint
names on the way across — `pelvis` lands on `Hips`, `upperarm_l` on `LeftArm`.

### What actually crosses

**A turn, not an orientation.** Each bone's rotation goes over measured from
its own rest pose, and Cascadeur folds its character's rest back in. Sending
the world orientation instead only works when both skeletons happen to rest the
same way, which they never do — Blender lays a bone's Y down its length, and
Cascadeur's joints rest however the character was built.

**Joint positions only when it is the same skeleton.** Onto a different
character only the root's position is sent and the rotations carry the pose,
so Cascadeur keeps its own bone lengths. Sending every joint's position drags
that character's bones to *this* rig's proportions, and the mesh — still bound
to the bones it was skinned to — breaks at the elbows and knees.

Measured, onto a character sent from here: joint positions match to
**0.0000 cm** and rotations to **0.000003**. Onto a different character the
pose is fitted rather than copied, and the character keeps its own proportions.

### Speed

A range crosses in one write. Forty frames of twenty-seven joints land in
0.09 s, so there is nothing to gain from sending frame by frame — a write
costs about the same whether it carries one pose or a thousand.

### Rigs built out of several skeletons

Some rigs are not one skeleton. A Rigify-built character — the Team Fortress 2
Trifecta rigs are the ones you are most likely to meet — keeps the bones that
move the mesh apart from the bones the hat, the pouch and the weapon hang off.
Sent as they are, the character arrives in Cascadeur in pieces: the body moves
and the props stay where they were.

**Unify Skeleton** merges them into one before the character crosses. Leave it
on. Your rig is not touched — the copy is made, sent and thrown away.

* The hat stays on the head, the pouch on the belt, the weapon in the hand.
* Fingers, toes and twist bones follow the limb they belong to.
* Both Trifecta rigs are recognised on sight, and so are their fingers.

### Filling Cascadeur's Rig Mode

Cascadeur's Quick Rigging Tool guesses which joint is which from their names,
and a character whose bones are not named the way it expects gets almost
nothing — a TF2 mercenary filled three fields out of twenty-one.

**Fill Rig Mode** hands it the answer instead. It runs on its own with
Send Character, and there is a button to do it again later.

Open the Quick Rigging Tool afterwards and look the joints over before you
build the rig. Cascadeur keeps the fields it has and quietly drops the rest;
anything it drops still follows the joint above it.

---

## Bringing it back

**Receive from Cascadeur** brings the work home. Pick the armature to receive
onto first.

**Animation Only** keys onto the rig you already have. **Mesh + Animation**
brings the character back as new objects.

On a rig that needed **Unify Skeleton** to get there, the animation lands on
the controls you normally animate with, not on the deform bones.

### Timing

Cascadeur runs on its own frame rate, which is usually not the one this scene
is set to. The same motion can come back as a different number of frames.

| Timing | What you get |
| --- | --- |
| **Match Timing** | It comes back at the speed it left, whatever Cascadeur is set to. |
| **Fit Scene Range** | Spread across this scene's frame range. Ten frames into two hundred and fifty is slow motion, and that is the point. |
| **One For One** | One Cascadeur frame becomes one Blender frame, untouched. |

Match Timing needs a Send Character from here to compare against; without one
the take lands at Cascadeur's own frame count and says so.

Setting both programs to the same frame rate is exact. Anything else is
interpolated, which is close but not the same thing.

---

## Settings worth knowing

| Setting | What it does |
| --- | --- |
| **Seed** | `-1` gives something new each time. Fix it to get the same motion back. |
| **Duration** | How long this take runs. |
| **Reduce Foot Skating** | Cleans up sliding feet **and** enforces waypoints and pose keys. Leave it on. |
| **Denoising Steps** | `0` uses the model's default. Fewer is faster and rougher. |
| **Prompt Strength** | How hard it tries to obey your words. |
| **Constraint Strength** | How hard it tries to hit your waypoints and poses. |

---

## When something is wrong

**"ARDY is still loading the model"** — the engine starts before it is ready.
Wait for *Ready*.

**"This bridge was started without a text encoder"** — Skip Text Encoder is
ticked. Untick it and reconnect.

**Connecting fails right away** — open the add-on preferences. If ARDY is not
**ready**, press **Install Engines**.

**The character walks on the spot** — that is the model, not a bug. Some seeds
produce a walk that barely travels. Change the seed, or ask for something with
more intent: *"a person runs forward quickly"*.

**Pose keys seem ignored** — make sure **Reduce Foot Skating** is on.

**The retarget looks broken** — check the family under Target matches the rig
you picked.

**"Cascadeur is not listening on 127.0.0.1:9564"** — its side is off. In
Cascadeur: `Animation Scripts` → **Receive Poses (Blender)**.

**Keyframes arrive but the character is turned the wrong way** — Cascadeur was
restarted after the character was sent. It only knows how to place the motion
if it still holds the record it made when the character arrived. Press **Send
Character** again, then send the keyframes.

**Keyframes go nowhere and nothing says why** — the joint names sent do not
match anything Cascadeur has open. Check the line above the buttons: it says
how many bones were matched and under which naming.

**Limbs bend oddly on Cascadeur's own character** — expected to a degree.
Cascadeur keeps its character's bone lengths, so a rig with different
proportions is fitted rather than copied.

**The panel says Connecting and the button is greyed out** — a session that
was closed mid-connect. Opening a file clears it; so does re-enabling the
add-on.

**Characters ignore their targets and walk off on their own headings** — fixed.
If you see it, the add-on is an older copy.

For anything else, Blender's system console (`Window ▸ Toggle System Console`)
carries the engine's own messages.
