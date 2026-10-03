# Wordle Arena

A daily five-letter word game you can play against everyone, or against your friends.

**Play now: https://wordleleader.replit.app**

No account or download needed. Pick a nickname and start guessing.

## How to play

Guess the hidden five-letter word in six tries. After each guess the tiles change color:

- **Green**: the letter is in the word and in the right spot.
- **Yellow**: the letter is in the word but in a different spot.
- **Grey**: the letter isn't in the word.

Every guess has to be a real word. Type on your keyboard or tap the on-screen keys. A timer starts on your first guess, and your time counts as a tiebreaker.

## Features

### The daily puzzle
Everyone gets the same word each day. A new one arrives at midnight UTC, numbered "No. 1", "No. 2" and so on. When you finish, **Copy result** gives you an emoji grid of your colors (no letters) to share.

### Global leaderboard
- **Today** ranks everyone who has finished today's puzzle: solved first, then fewest guesses, then fastest time. It stays hidden until you've finished yourself, so nobody gets a hint from other people's scores.
- **All-time** ranks players with at least 3 games by win rate, then average guesses, and shows their current streak.

### Your record
Games played, win percentage, current streak, best streak, and a chart of how many guesses your wins took.

### Compete with friends
Open the **Compete with friends** panel on the main page to set up a private competition, then share the invite link.

**Contests** run over several days:
- Choose a name, a start date and a length from 1 to 14 days.
- Each day the contest has its **own word**, separate from the daily puzzle. You can only play it on its day.
- The contest standings show everyone's points and a strip of their daily scores, with ✕ for a missed day. Today's scores stay hidden until you've played.
- When the last day ends, the contest shows its final results with the top three.

**Live races** happen in real time:
- Open a race room and share the link. Everyone who joins appears in the room, with a dot showing who's here now.
- The host starts each round. After a 3-second countdown, everyone gets the same word at once.
- Watch everyone's colored squares fill in as they guess. You never see their letters.
- A round ends when everyone has finished or after 5 minutes. Then the word is revealed and points are added to the room's scoreboard.
- The host can run as many rounds as the group likes. Anyone who arrives mid-round watches and joins from the next one.

**Scoring for contests and races:** a solve in 1 guess earns 6 points, a solve in 2 earns 5, down to 1 point for a solve in 6. A miss scores 0. If points are tied, the faster total time wins.

### Play on any device
- **Recovery code:** when you pick a nickname you get a recovery code, which is shown only once. Enter it on another phone or computer ("Played before on another device?") to bring your stats with you. If you lose it, you can get a new code under "Your record".
- **Reloading is safe:** if you close the page mid-game, your board is waiting when you come back.

### Accessibility
- **High contrast** swaps green and yellow for orange and blue, for colorblind players. Shared results use the same colors.
- The page follows your device's light or dark mode, and turns off animations if you've asked your device to reduce motion.

## Fair play
Answers stay on the server. Your browser only learns which letters were right or wrong after each guess, and sees the word once your game is over. Each player gets one attempt per daily puzzle and per contest day.

## Other versions
- **Solo play (no leaderboard):** https://amoeba11.github.io/wordle-arena/. Same game, with stats saved only in your browser.
- **Claude Artifact:** an internal version with its own leaderboard for members of one claude.ai organization.

## For developers
The whole game is the single page `index.html`, with no build step. The public server follows the contract in [server/SPEC.md](server/SPEC.md). [server/devserver.py](server/devserver.py) is a reference implementation you can run with `python3 server/devserver.py`. See [CLAUDE.md](CLAUDE.md) for how the project is put together and how the word lists are generated.
