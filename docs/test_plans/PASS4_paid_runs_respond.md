## Which cradle

cd ~/Documents/projects/cradle && export CANON_BIN=~/Documents/projects/canon-ai/.venv/bin/canon && npm run tauri dev

Q from testing: Can we delete the 2 "wrong ones?"

### Settings
So in settings, we have teh ability to "test" api keys, but in reality, this doesn't work. I checked it with the existing api keys in .env, only anthropic seems to support this. The others, I filled with newly created keys for this work and then saw that they still didn't work... so I don;t think this functionality works everywhere... 



## Step 0
Pass
### Step0a
- Checking out the settings and the test functionality doesn’t seem to work. I’m able to put keys in when I verified them fresh and then the tests fail. This is true for all of them except anthropic. I’m not sure if the others even have this… Do we need this anyways?
- 0D, 2026-09-10: openai 2 runs, kimi 2 runs; 3 of 4 passed. kimi just-talking first failed on an empty Moonshot balance and passed after a top-up; the error was wrongly labelled retryable because every 429 is treated as a rate limit - I did fix the balance issue. openai unbeatable-level called the correct two tools in the reverse order. That's a failure of the eval's strict-order check, not of the provider: nothing in the task requires validate before describe. Tool calling works on both providers. A8 stays open until the full corpus runs.

### Step 0B
pass

## LegC Paid and Unpaid
Unpaid "ran" fine, but it's all canned, and aside from test stuff pretty useless? I don't think we need so much fake/canned stuff everywhere... maybe just in create, but not in all the app throughout. Definitely we don't need both "Fake" and "$0 deterministic" or whatever. 
### issues 
1)  Estimated cost seems useless right now?
```
    Estimated cost · paid run
    not estimated
    Re-authors Char 0's default tree · 3 nodes · 4 choices in one request. Key read from CANON_ENV_FILE; a missing one is refused up front with the variable named.
``` 

If we’re not going to make a suggestion… why show this at all? Either show some information on cost here or don’t. 
2) Edit prompt (advanced) doesn’t do anything. I can’t tell if its supposed to be a button or what, but I can’t click it… 
3) I’ve tried to generate a new tree with anthropic and it’s terrible. I saw
```
PAID
improve Char 0's default tree
estimate
— not estimated
backend anthropic · default model
work
3 nodes · 4 choices in one request
budget
$0 spent today · no cap set (remove no cap - we don't want this stuff here anyways)
Accept · spend on anthropic 
Reject
```
I hit “Accept” but I don’t see if it’s running, no information about it waiting, nothing in the terminal where it’s running… so no idea if it’s working… I Can’t click away while it’s running (I assume it’s running). No idea on how long it’ll take. I’ll wait 5 minutes but if it doesn’t report back, I’m afraid I’m going to need a rework of this page and how we do this - we should have constant feedback to the users, and we have to make sure any generations land… 
4) Oh boy do I hate this copy: “LLM re-author on anthropic — a paid run, and still only a proposal Nothing proposed. The prose is already clean by this backend's rules — which is an answer, not a failure.” 
    -Just say “LLM re-author with {model provider}” And whatever “clean” means, maybe just say if it’s valid or not… 

interesting, I’m not seeing it land on the anthropic api console for history… 
I’ve canceled this, but I think it’s fair to say this failed...

## Leg D Provider swap 

Leg D, 2026-09-10: 5 runs. just-talking passed on both providers (Kimi's first attempt hit an empty Moonshot balance, which was wrongly labelled retryable). unbeatable-level: OpenAI failed, Kimi passed and then failed on a rerun. Both providers call the correct two tools, in whichever order they happen to pick. The failures come from the eval's strict-order check, not from the providers. Tool calling works on both. A8 stays open. Fix the order check before the full-corpus run, or up to 4 of its 9 conversations will fail on ordering alone.

## Leg A - The dungeon run

path `~/CradleProjects/phase4_test2_paid`
just a side note, but these are horribly named. Why step 0, then legs, alternating C/D and then A??

- okay, while running we have the timer, we have a progress bar, we have a don't close. We need this sort of display also on the npc tree generation, really anywhere generations are in cradle inside a project. We should also be able to click away INSIDE projects, but check the job tray to return to that screen. 
- should post things to terminal... we should have real logs - both in the terminal but somewhere in cradle so users can get bug reports on failures to download and share to us in case of failures. need to really up our game here
- Thinking on it, we'll need to improve the create flow... I didn't write a prompt anywhere for this meaning we have a hard coded world prompt... that is a later phase thing though
- during the initial generation, I'd like to show assets to the user as theyre being generated. Maybe we scroll through the dungeon or block versions of a platformer as the rest is being generated. if/when we start to generate enemy portraits, characters, etc we should add those to the rotation so users can at least see some generations come through. We can label them with their names if they've been named, but it's just another visual to add to the waiting period so they can get hyped about stuff as it's being made... does this make sense? 

```
wolfgangblack@mac canon-ai % uv run canon spend list ~/CradleProjects/phase4_test2_paid
{
  "canon_version": "0.1",
  "result": "spend_list",
  "spend": {
    "count": 1,
    "total_actual_usd": 3.001468,
    "total_estimate_usd": 3.9952,
    "by_op": {
      "world": {
        "count": 1,
        "actual_usd": 3.001468,
        "estimate_usd": 3.9952
      }
    },
    "entries": [
      {
        "schema": "cradle-spend/v1",
        "ts": "2026-09-11T05:58:32+00:00",
        "actual_usd": 3.001467999999999,
        "backends": {
          "image": "fal",
          "llm": "anthropic",
          "music": "lyria",
          "sfx": "elevenlabs",
          "vlm": "none"
        },
        "estimate": {
          "best": 3.9952,
          "worst": 6.4107
        },
        "op": "world",
        "scope": "world"
      }
    ]
  }
}
```

```
cat ~/CradleProjects/phase4_test2_paid/generation_stats.json 
{
  "llm_backend": "anthropic",
  "image_backend": "fal",
  "music_backend": "lyria",
  "sfx_backend": "elevenlabs",
  "assets_placeholder": false,
  "llm_calls": 83,
  "total_input_tokens": 81781,
  "total_output_tokens": 61275,
  "total_cost": 1.164468,
  "input_tokens": 81781,
  "output_tokens": 61275,
  "total_tokens": 143056,
  "image_attempts": 50,
  "image_successes": 43,
  "images_attempted": 50,
  "images_succeeded": 43,
  "music_attempted": 8,
  "music_succeeded": 0,
  "sfx_attempted": 15,
  "sfx_succeeded": 4,
  "llm_cost_usd": 1.164468,
  "image_cost_usd": 1.676999999999999,
  "audio_cost_usd": 0.16,
  "total_cost_usd": 3.001467999999999,
  "generation_time_seconds": 0.0,
  "generation_time_human": "0m 00s",
  "by_phase": {
    "story": {
      "calls": 1,
      "input_tokens": 436,
      "output_tokens": 1538,
      "cost": 0.024378
    },
    "classes": {
      "calls": 4,
      "input_tokens": 2515,
      "output_tokens": 944,
      "cost": 0.021705000000000002
    },
    "warrior:ability": {
      "calls": 1,
      "input_tokens": 560,
      "output_tokens": 640,
      "cost": 0.011280000000000002
    },
    "mage:spell": {
      "calls": 1,
      "input_tokens": 556,
      "output_tokens": 640,
      "cost": 0.011268
    },
    "healer:spell": {
      "calls": 1,
      "input_tokens": 556,
      "output_tokens": 640,
      "cost": 0.011268
    },
    "jester:spell": {
      "calls": 1,
      "input_tokens": 556,
      "output_tokens": 640,
      "cost": 0.011268
    },
    "jester:ability": {
      "calls": 1,
      "input_tokens": 565,
      "output_tokens": 375,
      "cost": 0.00732
    },
    "db:item": {
      "calls": 9,
      "input_tokens": 8416,
      "output_tokens": 1713,
      "cost": 0.050942999999999995
    },
    "db:monster": {
      "calls": 6,
      "input_tokens": 6560,
      "output_tokens": 4366,
      "cost": 0.08517
    },
    "db:npc": {
      "calls": 6,
      "input_tokens": 6124,
      "output_tokens": 2317,
      "cost": 0.053126999999999994
    },
    "db:event": {
      "calls": 12,
      "input_tokens": 13664,
      "output_tokens": 7170,
      "cost": 0.148542
    },
    "db:quest": {
      "calls": 6,
      "input_tokens": 6548,
      "output_tokens": 1463,
      "cost": 0.04158900000000001
    },
    "dialogue": {
      "calls": 24,
      "input_tokens": 30205,
      "output_tokens": 34790,
      "cost": 0.612465
    },
    "spell_pool:mage_damage": {
      "calls": 1,
      "input_tokens": 556,
      "output_tokens": 800,
      "cost": 0.013668
    },
    "spell_pool:healer_damage": {
      "calls": 1,
      "input_tokens": 556,
      "output_tokens": 800,
      "cost": 0.013668
    },
    "spell_pool:heal": {
      "calls": 1,
      "input_tokens": 556,
      "output_tokens": 800,
      "cost": 0.013668
    },
    "spell_pool:buff": {
      "calls": 1,
      "input_tokens": 556,
      "output_tokens": 800,
      "cost": 0.013668
    },
    "narrative": {
      "calls": 6,
      "input_tokens": 2296,
      "output_tokens": 839,
      "cost": 0.019473
    }
  }
}%                                      
```

- We did fail at a few images: 2 npcs, 1 monster, 2 quests, 2 events ()
- Stepping between rooms in the dungeon crawler should respect the zoom so I can see levels at the same aspect/ratio and not have to zoom in/out every time. 
- Music didn't seem to go through, even though it's shown in logs - I can't access it in cradle for this project...

## Leg B — the platformer run
path `~/CradleProjects/pass4_test3_plat_paid`

First off - why is animation "Claude"?

```
Estimated cost
$2.67–$3.30
LLM $0.212 · images 54×→$2.11 · music $0.080 · sfx $0.160 · anim $0.108
```

Note: these number are 'off' because I did animation != Fake, with animation fake we have the right numbers


## Notes:
Don't forget the bugs you found while writing your pass 4


all generations look good - no music though, only sfx...

```
 uv run canon spend list ~/CradleProjects/pass4_test3_plat_paid  
{
  "canon_version": "0.1",
  "result": "spend_list",
  "spend": {
    "count": 1,
    "total_actual_usd": 0.0,
    "total_estimate_usd": 2.6656,
    "by_op": {
      "world": {
        "count": 1,
        "actual_usd": 0.0,
        "estimate_usd": 2.6656
      }
    },
    "entries": [
      {
        "schema": "cradle-spend/v1",
        "ts": "2026-09-11T06:23:17+00:00",
        "actual_usd": 0,
        "backends": {
          "image": "fal",
          "llm": "anthropic",
          "music": "lyria",
          "sfx": "elevenlabs",
          "vlm": "anthropic"
        },
        "estimate": {
          "best": 2.6656,
          "worst": 3.3004
        },
        "op": "world",
        "scope": "world"
      }
    ]
  }
}
```

```
cat ~/CradleProjects/pass4_test3_plat_paid/generation_stats.json 
{
  "llm_backend": "anthropic",
  "image_backend": "fal",
  "music_backend": "lyria",
  "sfx_backend": "elevenlabs",
  "assets_placeholder": false,
  "llm_calls": 52,
  "total_input_tokens": 104313,
  "total_output_tokens": 23648,
  "total_cost": 0.41148000000000007,
  "input_tokens": 104313,
  "output_tokens": 23648,
  "total_tokens": 127961,
  "image_attempts": 52,
  "image_successes": 52,
  "images_attempted": 52,
  "images_succeeded": 52,
  "music_attempted": 0,
  "music_succeeded": 0,
  "sfx_attempted": 4,
  "sfx_succeeded": 4,
  "llm_cost_usd": 0.41148000000000007,
  "image_cost_usd": 2.028,
  "audio_cost_usd": 0.16,
  "total_cost_usd": 2.5994800000000002,
  "generation_time_seconds": 0.0,
  "generation_time_human": "0m 00s",
  "by_phase": {
    "plat:world": {
      "calls": 1,
      "input_tokens": 377,
      "output_tokens": 163,
      "cost": 0.0023840000000000003
    },
    "plat:stage:hollow_roots": {
      "calls": 1,
      "input_tokens": 899,
      "output_tokens": 699,
      "cost": 0.008788
    },
    "plat:style:hollow_roots": {
      "calls": 1,
      "input_tokens": 591,
      "output_tokens": 127,
      "cost": 0.002452
    },
    "plat:enemies:0": {
      "calls": 1,
      "input_tokens": 334,
      "output_tokens": 58,
      "cost": 0.000624
    },
    "plat:enemies:1": {
      "calls": 1,
      "input_tokens": 355,
      "output_tokens": 54,
      "cost": 0.000625
    },
    "plat:enemies:2": {
      "calls": 1,
      "input_tokens": 357,
      "output_tokens": 56,
      "cost": 0.000637
    },
    "plat:enemies:3": {
      "calls": 1,
      "input_tokens": 363,
      "output_tokens": 55,
      "cost": 0.000638
    },
    "plat:items:0": {
      "calls": 1,
      "input_tokens": 252,
      "output_tokens": 45,
      "cost": 0.000477
    },
    "plat:items:1": {
      "calls": 1,
      "input_tokens": 261,
      "output_tokens": 44,
      "cost": 0.000481
    },
    "plat:items:2": {
      "calls": 1,
      "input_tokens": 279,
      "output_tokens": 45,
      "cost": 0.000504
    },
    "plat:items:3": {
      "calls": 1,
      "input_tokens": 277,
      "output_tokens": 57,
      "cost": 0.000562
    },
    "plat:layout:l1:s0": {
      "calls": 2,
      "input_tokens": 5972,
      "output_tokens": 588,
      "cost": 0.017824
    },
    "plat:layout:l1:s1": {
      "calls": 3,
      "input_tokens": 10177,
      "output_tokens": 2432,
      "cost": 0.044674000000000005
    },
    "plat:layout:l1:s2": {
      "calls": 3,
      "input_tokens": 10090,
      "output_tokens": 2432,
      "cost": 0.0445
    },
    "plat:layout:l1r1:s0": {
      "calls": 2,
      "input_tokens": 6152,
      "output_tokens": 626,
      "cost": 0.018564
    },
    "plat:layout:l2:s0": {
      "calls": 2,
      "input_tokens": 5974,
      "output_tokens": 605,
      "cost": 0.017998
    },
    "plat:layout:l2:s1": {
      "calls": 3,
      "input_tokens": 10195,
      "output_tokens": 2432,
      "cost": 0.04471
    },
    "plat:layout:l2:s2": {
      "calls": 3,
      "input_tokens": 9961,
      "output_tokens": 2432,
      "cost": 0.044242000000000004
    },
    "plat:layout:l2:s3": {
      "calls": 3,
      "input_tokens": 10171,
      "output_tokens": 2432,
      "cost": 0.04466200000000001
    },
    "plat:layout:l2r1:s0": {
      "calls": 3,
      "input_tokens": 9181,
      "output_tokens": 2432,
      "cost": 0.042682
    },
    "plat:layout:l2r1:s1": {
      "calls": 3,
      "input_tokens": 10027,
      "output_tokens": 2432,
      "cost": 0.044374
    },
    "plat:placement:l1": {
      "calls": 2,
      "input_tokens": 2538,
      "output_tokens": 337,
      "cost": 0.004223
    },
    "plat:placement:l2": {
      "calls": 3,
      "input_tokens": 4024,
      "output_tokens": 497,
      "cost": 0.006509
    },
    "plat:placement:l2r1": {
      "calls": 1,
      "input_tokens": 1092,
      "output_tokens": 90,
      "cost": 0.001542
    },
    "plat:item_placement:l1": {
      "calls": 1,
      "input_tokens": 838,
      "output_tokens": 365,
      "cost": 0.0026630000000000004
    },
    "plat:item_placement:l1r1": {
      "calls": 1,
      "input_tokens": 888,
      "output_tokens": 520,
      "cost": 0.003488
    },
    "plat:item_placement:l2": {
      "calls": 1,
      "input_tokens": 845,
      "output_tokens": 553,
      "cost": 0.00361
    },
    "plat:item_placement:l2r1": {
      "calls": 1,
      "input_tokens": 796,
      "output_tokens": 460,
      "cost": 0.0030960000000000002
    },
    "plat:decorator:l1": {
      "calls": 1,
      "input_tokens": 252,
      "output_tokens": 145,
      "cost": 0.000977
    },
    "plat:decorator:l1r1": {
      "calls": 1,
      "input_tokens": 279,
      "output_tokens": 145,
      "cost": 0.0010040000000000001
    },
    "plat:decorator:l2": {
      "calls": 1,
      "input_tokens": 244,
      "output_tokens": 145,
      "cost": 0.000969
    },
    "plat:decorator:l2r1": {
      "calls": 1,
      "input_tokens": 272,
      "output_tokens": 145,
      "cost": 0.000997
    }
  }
}%                              
```