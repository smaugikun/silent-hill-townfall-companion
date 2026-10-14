// A bounded startup/short-gap allowance avoids giving the first word or every tiny buffer gap to PC speakers.
// Suspension, hiding, disabling sound and a sustained failure still give audio back immediately or shortly after.
export function createAudioHandoff(now = () => performance.now()) {
  let wantedBefore = false, enteredAt = -Infinity, heardAt = -Infinity;
  return ({wanted, running, available = true, playing}) => {
    const time = now();
    if (wanted && !wantedBefore) enteredAt = time;
    wantedBefore = wanted;
    if (!wanted || !running || !available) { heardAt = -Infinity; return false; }
    if (playing) heardAt = time;
    return playing || time - enteredAt < 500 || time - heardAt < 300;
  };
}
