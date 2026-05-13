# Real-time live leaderboard for Super Smash Bros Ultimate

## Tools
- Nintendo Switch
- EVGA Capture Card
- Google Gemini API (default model: Gemini 3.1 Pro Preview)

## How it works
Matches are automatically detected & recorded by a computer program connected to the capture card that is continuously monitoring the nintendo switch. 
Once a match is finished, a clip plus high-resolution stills from the results screen are sent to Gemini to extract stats from, after which the leaderboard is updated based on the match's stats.
