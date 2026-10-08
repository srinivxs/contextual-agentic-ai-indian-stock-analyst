/**
 * What a visitor reads when nothing answers behind /api (the owner, 2026-10-09). The AWS stack is
 * switched off between sessions on purpose to keep the cost down, while the static site stays up
 * on CloudFront, so this explains the quiet instead of showing an error.
 */
export function DemoOffline() {
  return (
    <section className="demo-offline" aria-labelledby="demo-offline-title">
      <h2 id="demo-offline-title">The live demo is switched off</h2>
      <p>
        It runs on AWS, and its owner switches it off between sessions on purpose, to keep the cost
        down. Nothing is broken.
      </p>
      <p>
        Want to try it? Ask the person who shared this link to switch it on: it takes about 15
        minutes.
      </p>
    </section>
  );
}
