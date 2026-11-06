"""aiHair's hair features behind one interface, for the server
(ai_hair/pipeline.py) and batch_main.py.

core.py      the Feature interface, the photo and run context a feature
             gets, the EffectResult it returns, and the billed calls
             (images.edit, the analyst's responses.parse) with their costs.
common.py    what more than one feature shares: the one-call and composite
             bases, the composite that pastes renders back lined up region
             by region, the pyramid blend, the Gaussian.
imaging.py   image helpers the features share: renders sized back to the
             photo, masks, overlays, review sheets.
align.py     lining a render up with the photo it was drawn from.
features/    the nine features, one module each, by name in
             features.EFFECTS.
"""
