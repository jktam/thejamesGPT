# The James Bot

## Commands

### General
* **/jhelp**: command list
* **!jhoose**: randomly choose a single option from a comma separated input
* **!eats <city> <miles> [optional: category]**: get list of restaurants within given radius in miles (default 35 miles); optional food category accepted (e.g. Chinese). _hint: use quotes to enclose multiple words_
* **!addy <restaurant_name> <city>**: get address of restaurant. _hint: use quotes to enclose multiple words_
### Degeneracy
* **/blackjack <bet_amount> [optional: "join"]**: *BETA* - start blackjack session ($20 minimum bet)
* **/blackjack_start**: *BETA* - start multiplayer blackjack session
* **/balance**: check balance
* **/resetbalance**: reset balance to default of $1000
* **!leaderboard**: show historical high balances
### OpenAI actions
* **!jpt <prompt>** : classic chatgpt text response
* **!jimg**: generate image given prompt
* **!jedit <prompt> +ATTACHED_IMAGE** : given png image with transparency, ai will fill the transparent space with info from the prompt
* **!jvari +ATTACHED_IMAGE** : given image, generate random variation