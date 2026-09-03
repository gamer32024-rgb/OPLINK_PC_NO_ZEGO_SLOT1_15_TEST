# Protocol

Protocol notes for the Pico CDC Serial command channel.

Initial commands:

```text
PING <seq>
STATUS <seq>
RESET <seq>
DOWN <seq> <contact_id> <x> <y>
MOVE <seq> <contact_id> <x> <y>
UP <seq> <contact_id> <x> <y>
CANCEL <seq>
```
