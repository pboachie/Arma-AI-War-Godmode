# Everon imagery and place coordinates

Everon is a fictional Arma Reforger terrain by Bohemia Interactive a.s. This is a
community visualization, not a Bohemia Interactive product.

- **Imagery carried over unchanged:** Reforger Command Center's
  [4,096 × 4,096 satellite texture](https://reforgercommandcenter.com/data/tex.jpg)
  ([source and credits](https://reforgercommandcenter.com/)). RCC resampled the
  in-game screenshot tiles by Bewilderbeest (Nick Ludlam) from
  [EnfusionMapMaker](https://reforger.recoil.org/everon/). RCC identifies both
  the original and its derived imagery as APL-SA.
- **Landmark data used in the overlay:** a subset of the
  [EnfusionMapMaker Everon locations](https://github.com/nickludlam/EnfusionMapMaker/blob/39877e03991cd7c6a0e2bf9fb063f60b19cdb69f/Web/everon/everon-locations.js)
  by Bewilderbeest. Some labels are expanded or formatted for display.
- **License for the imagery and the landmark data:** [Arma Public License Share
  Alike (APL-SA)](https://www.bohemia.net/en/licenses/arma-public-license-share-alike).
  This permits attributed, noncommercial use for Arma. The project code's
  repository license does not relicense this material.

The checked-in JPEG SHA-256 is
`c2c86822d9b3aaa49c5defbd8b1f09d2c982f6cbd0dea5b307cb74b029e50d63`.
The texture spans 0–12,800 m on each axis with north at the top. Overlay
positions use Enfusion coordinates `[X east, Y altitude, Z north]`, projected
to `(X, 12800 - Z)`. The airport, Morton, Lamentin, Chotain and Saint-Pierre
are useful visual calibration references. Terrain or structures added by a
scenario are not drawn by this static satellite capture.
