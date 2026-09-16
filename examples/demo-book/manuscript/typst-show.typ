// How the PDF opens. Quarto uses this partial to invoke the Typst book
// template, and replacing it is the only way to put anything before the
// template's first page, which is what a cover is.
//
// A printed book's cover is a separate wrap on different stock, so a
// press-ready interior would leave this out: a full-bleed page inside the
// block also puts every conventional leaf on the wrong side of the sheet.
// This PDF is a reading copy rather than a print interior, and a reading
// copy that opens on a blank title page is worse than one that opens on
// the cover.
//
// `book.typst_template` in quirework.toml names this file, which is where
// `quirework book fonts` looks for the faces the print build requires. It
// declares none, so the build asks only for the faces the figures use.

#import "@preview/orange-book:0.7.1": book, part, chapter, appendices

// The cover, full bleed and without a folio, emitted before the show rule
// wraps the rest of the document. `cover.png` sits beside this file in the
// staged tree, the same file `cover-image` hands to the HTML and the EPUB,
// so all three editions open on one image.
// `contain` rather than `cover`: the art is 2:3 and the page is not, and
// cropping to fill took the top off the title and the byline off the foot.
// The page is filled with the art's own paper color, so the letterboxing
// at the sides reads as margin rather than as a mistake. The value is the
// paper in illustrations/STYLE.md, which is where this book's colors are
// decided.
#page(margin: 0pt, header: none, footer: none, numbering: none,
      fill: rgb("#f7f4ee"))[
  #image("cover.png", width: 100%, height: 100%, fit: "contain")
]

#show: book.with(
$if(title)$
  title: [$title$],
$endif$
$if(subtitle)$
  subtitle: [$subtitle$],
$endif$
$if(by-author)$
  author: "$for(by-author)$$it.name.literal$$sep$, $endfor$",
$endif$
$if(lang)$
  lang: "$lang$",
$endif$
$if(toc-depth)$
  outline-depth: $toc-depth$,
$endif$
)
