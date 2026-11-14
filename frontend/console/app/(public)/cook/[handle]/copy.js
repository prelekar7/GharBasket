// Copy of the public cook page, English and Hindi, from Cook.dc.html. Lines marked (new) are not
// in the design (it only shows Lata, and predates M8 §3.5's contract) and need a native-speaker review.
export const COPY = {
  en: {
    kind: "Know your cook",
    dishesTitle: "Cooks for GharBasket",
    trainingsTitle: "Training passed", // (new)
    book: "Book a tiffin in the app", // (new: the design's "Book her tiffin"; M8 records no gender)
    privacy: "Shown with {name}’s consent. No phone number or address is ever shared.", // (new)
    fssaiPill: "FSSAI registered",
    hygienePill: "Hygiene trained",
    fssai: "FSSAI registration",
    fssaiUntil: "{reg} · valid until {until}", // (new)
    inspection: "Kitchen inspection", // (new)
    inspectionValue: "Grade {grade} · {date}", // (new)
    rating: "Families’ rating",
    ratingValue: "{average} · {count} ratings", // (new: the design's "last 30 days")
    noRating: "Not enough ratings yet", // (new)
    checkedBy: "Checked by",
    checkedByValue: "{hub}",
    since: "Cooking with GharBasket since", // (new)
    /** @type {Record<string, string>} M8 SKILLS (partners/models.py) */
    skills: {
      veg: "Vegetarian",
      jain: "Jain",
      no_onion_garlic: "No onion or garlic",
      diabetic: "Diabetic-friendly",
      egg: "Egg",
      nonveg: "Non-vegetarian",
    }, // (new)
  },
  hi: {
    kind: "अपनी रसोइया को जानें",
    dishesTitle: "GharBasket के लिए बनाती हैं",
    trainingsTitle: "पूरी की गई ट्रेनिंग", // (new)
    book: "ऐप में टिफ़िन बुक करें", // (new)
    privacy: "{name} की सहमति से दिखाया गया। फ़ोन नंबर या पता कभी साझा नहीं होता।", // (new)
    fssaiPill: "FSSAI पंजीकृत", // (new)
    hygienePill: "साफ़-सफ़ाई प्रशिक्षित", // (new)
    fssai: "FSSAI रजिस्ट्रेशन",
    fssaiUntil: "{reg} · {until} तक मान्य", // (new)
    inspection: "रसोई की जाँच", // (new)
    inspectionValue: "ग्रेड {grade} · {date}", // (new)
    rating: "परिवारों की रेटिंग",
    ratingValue: "{average} · {count} रेटिंग", // (new)
    noRating: "अभी पर्याप्त रेटिंग नहीं", // (new)
    checkedBy: "जाँच की",
    checkedByValue: "{hub} ने",
    since: "GharBasket के साथ कब से", // (new)
    /** @type {Record<string, string>} */
    skills: {
      veg: "शाकाहारी",
      jain: "जैन",
      no_onion_garlic: "बिना प्याज़-लहसुन",
      diabetic: "डायबिटीज़ के अनुकूल",
      egg: "अंडा",
      nonveg: "मांसाहारी",
    }, // (new)
  },
};
