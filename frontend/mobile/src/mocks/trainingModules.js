// M8 training modules (§3.2): short lessons, each ending in a quiz, in English and Hindi (Marathi
// falls back to English, as the spec says). 5 to 10 questions per module, 80% to pass. Each question:
// [text, options, index of the right answer]. The right answers stay here, on the "server": GET
// /v1/trainings never sends them and POST /v1/trainings/{code}/complete scores what it gets.
// Which modules a person needs follows the kind of their application or partner record.

/** @typedef {[string, string]} EnHi */
/** @typedef {[EnHi, EnHi[], number]} Question */

/** Required modules per partner kind, in the order to take them (handbook 13 §13.8). */
export const REQUIRED = /** @type {Record<string, string[]>} */ ({
  kitchen: ["app_basics", "hygiene", "kitchen_ops"],
  captain: ["app_basics", "captain_handover"],
  rider: ["app_basics", "rider_safety"],
  packer: ["app_basics", "hygiene", "packing"],
});

/** @type {{ code: string, version: number, title: EnHi, summary: EnHi, minutes: number, for_kinds: string[], questions: Question[] }[]} */
export const MODULES = [
  {
    code: "app_basics",
    title: ["Using the app", "ऐप का इस्तेमाल"],
    summary: [
      "Scan, confirm and get paid, even without signal.",
      "स्कैन करें, पुष्टि करें और कमाई पाएँ, नेटवर्क न हो तब भी।",
    ],
    minutes: 6,
    for_kinds: ["kitchen", "captain", "rider", "packer"],
    questions: [
      [
        ["When are your earnings paid?", "आपकी कमाई कब मिलती है?"],
        [
          ["Every week, to your bank", "हर हफ़्ते, आपके बैंक में"],
          ["In cash from the rider", "राइडर से नकद"],
          ["Once a year", "साल में एक बार"],
        ],
        0,
      ],
      [
        ["A customer asks for money back in cash. What do you do?", "ग्राहक नकद पैसे वापस माँगे तो?"],
        [
          ["Pay from your pocket", "अपनी जेब से दे दें"],
          ["Tell them refunds come through the app", "बताएँ कि रिफ़ंड ऐप से आता है"],
          ["Ignore them", "अनसुना कर दें"],
        ],
        1,
      ],
      [
        [
          "Someone calls asking for your OTP to “release a payment”.",
          "कोई फ़ोन करके “पेमेंट छोड़ने” के लिए OTP माँगता है।",
        ],
        [
          ["Share it", "बता दें"],
          ["Share half of it", "आधा बता दें"],
          ["Never share it; tell the hub", "कभी न बताएँ; हब को ख़बर करें"],
        ],
        2,
      ],
      [
        ["How do you confirm you handed over a bag?", "बैग सौंपने की पुष्टि कैसे करें?"],
        [
          ["Scan the bag’s QR code in the app", "ऐप में बैग का QR कोड स्कैन करें"],
          ["Tell a neighbour", "पड़ोसी को बता दें"],
          ["Send a text to the hub", "हब को मैसेज भेजें"],
        ],
        0,
      ],
      [
        [
          "Your phone has no signal at the door. What do you do?",
          "दरवाज़े पर फ़ोन में नेटवर्क नहीं है। क्या करें?",
        ],
        [
          ["Skip the delivery", "डिलीवरी छोड़ दें"],
          [
            "Carry on; the app saves your taps and sends them when you are back online",
            "जारी रखें; ऐप आपके टैप सहेजकर नेटवर्क आने पर भेज देता है",
          ],
          ["Come back tomorrow", "कल आएँ"],
        ],
        1,
      ],
    ],
    version: 1,
  },
  {
    code: "hygiene",
    title: ["Clean hands, clean kitchen", "साफ़ हाथ, साफ़ रसोई"],
    summary: ["Clean hands, covered hair, a clean counter.", "साफ़ हाथ, ढके बाल, साफ़ प्लेटफ़ॉर्म।"],
    minutes: 10,
    for_kinds: ["kitchen", "packer"],
    questions: [
      [
        ["Before you start cooking, what comes first?", "खाना बनाना शुरू करने से पहले सबसे पहले क्या?"],
        [
          ["Wash hands with soap for 20 seconds", "साबुन से 20 सेकंड हाथ धोएँ"],
          ["Wipe hands on your dupatta", "दुपट्टे से हाथ पोंछ लें"],
          ["Rinse hands with water only", "सिर्फ़ पानी से हाथ धो लें"],
        ],
        0,
      ],
      [
        ["You have a cut on your finger. What do you do?", "उँगली पर कट लगा है। आप क्या करेंगी?"],
        [
          ["Cook as usual", "हमेशा की तरह खाना बनाएँ"],
          ["Cover it with a waterproof bandage and wear a glove", "वॉटरप्रूफ़ पट्टी लगाकर दस्ताना पहनें"],
          ["Wash it and leave it open", "धोकर खुला छोड़ दें"],
        ],
        1,
      ],
      [
        ["When do you clean the counter?", "रसोई का प्लेटफ़ॉर्म कब साफ़ करें?"],
        [
          ["Once a week", "हफ़्ते में एक बार"],
          ["Before and after cooking", "खाना बनाने से पहले और बाद में"],
          ["Only when it looks dirty", "सिर्फ़ गंदा दिखने पर"],
        ],
        1,
      ],
      [
        ["How often should you change the kitchen cloth?", "रसोई का कपड़ा कितनी बार बदलें?"],
        [
          ["Once a month", "महीने में एक बार"],
          ["Every day, and wash it hot", "रोज़, और गरम पानी में धोएँ"],
          ["Only when it tears", "सिर्फ़ फटने पर"],
        ],
        1,
      ],
      [
        ["What do you do with long hair while cooking?", "खाना बनाते समय लंबे बाल कैसे रखें?"],
        [
          ["Tie it back and cover it", "बाँधकर ढक लें"],
          ["Leave it open", "खुले छोड़ दें"],
          ["Tuck it in the collar", "कॉलर में दबा लें"],
        ],
        0,
      ],
    ],
    version: 1,
  },
  {
    code: "kitchen_ops",
    title: ["Kitchen routine: storage and handover", "रसोई का तरीक़ा: रखना और सौंपना"],
    summary: [
      "Keep food safe, and have tiffins ready for the rider on time.",
      "खाना सुरक्षित रखें और राइडर के लिए टिफ़िन समय पर तैयार रखें।",
    ],
    minutes: 14,
    for_kinds: ["kitchen"],
    questions: [
      [
        ["Where do you keep paneer and curd?", "पनीर और दही कहाँ रखें?"],
        [
          ["In the fridge, covered", "फ़्रिज में, ढककर"],
          ["On the kitchen shelf", "रसोई की शेल्फ़ पर"],
          ["Near the stove to keep warm", "गरम रखने के लिए चूल्हे के पास"],
        ],
        0,
      ],
      [
        ["How do you keep atta and dal?", "आटा और दाल कैसे रखें?"],
        [
          ["In the open sack", "खुली बोरी में"],
          ["In airtight containers, off the floor", "हवाबंद डिब्बों में, ज़मीन से ऊपर"],
          ["Under the sink", "सिंक के नीचे"],
        ],
        1,
      ],
      [
        [
          "Cooked dal is left after lunch. What do you do?",
          "दोपहर के बाद पकी दाल बची है। आप क्या करेंगी?",
        ],
        [
          ["Keep it on the stove for dinner", "रात के लिए चूल्हे पर ही रखें"],
          [
            "Cool it, cover it and put it in the fridge within 2 hours",
            "ठंडी करके, ढककर 2 घंटे के अंदर फ़्रिज में रखें",
          ],
          ["Pack it in tomorrow’s tiffins", "कल के टिफ़िन में डाल दें"],
        ],
        1,
      ],
      [
        ["How do raw and cooked food sit in the fridge?", "फ़्रिज में कच्चा और पका खाना कैसे रखें?"],
        [
          ["Cooked above raw, both covered", "पका ऊपर, कच्चा नीचे, दोनों ढके"],
          ["Raw food on top", "कच्चा खाना ऊपर"],
          ["Together in one bowl", "दोनों एक ही बर्तन में"],
        ],
        0,
      ],
      [
        ["The milk smells sour. What do you do?", "दूध से खट्टी गंध आ रही है। आप क्या करेंगी?"],
        [
          ["Boil it and use it", "उबालकर इस्तेमाल करें"],
          ["Mix it with fresh milk", "ताज़े दूध में मिला दें"],
          ["Don’t use it for tiffins", "टिफ़िन में इस्तेमाल न करें"],
        ],
        2,
      ],
      [
        ["When must tiffins be ready for the rider?", "राइडर के लिए टिफ़िन कब तैयार हों?"],
        [
          ["By the pickup time in the app", "ऐप में दिए पिकअप समय तक"],
          ["Whenever they are cooked", "जब भी बन जाएँ"],
          ["After lunch", "दोपहर के खाने के बाद"],
        ],
        0,
      ],
      [
        ["You can’t cook tomorrow. What do you do?", "कल आप खाना नहीं बना पाएँगी। क्या करें?"],
        [
          ["Ask a neighbour to cook", "पड़ोसी से बनवा लें"],
          ["Tell the hub in the app before 9:30 PM", "रात 9:30 से पहले ऐप में हब को बताएँ"],
          ["Don’t answer the door", "दरवाज़ा न खोलें"],
        ],
        1,
      ],
      [
        ["How do you hand tiffins to the rider?", "राइडर को टिफ़िन कैसे दें?"],
        [
          ["Counted, in the crate, marked Ready in the app", "गिनकर, क्रेट में, ऐप में 'तैयार' दबाकर"],
          ["Loose in a bag", "थैली में खुले"],
          ["Outside the door", "दरवाज़े के बाहर"],
        ],
        0,
      ],
    ],
    version: 1,
  },
  {
    code: "packing",
    title: ["Packing", "पैक करना"],
    summary: [
      "Close, label and crate every box the same way.",
      "हर डिब्बा एक ही तरह बंद करें, लेबल लगाएँ और क्रेट में रखें।",
    ],
    minutes: 8,
    for_kinds: ["packer"],
    questions: [
      [
        ["When do you close the tiffin?", "टिफ़िन कब बंद करें?"],
        [
          ["While the food is still steaming", "जब खाना भाप छोड़ रहा हो"],
          ["Once the steam stops, within 30 minutes", "भाप रुकने के बाद, 30 मिनट के अंदर"],
          ["The next morning", "अगली सुबह"],
        ],
        1,
      ],
      [
        ["What goes on every tiffin?", "हर टिफ़िन पर क्या लगेगा?"],
        [
          ["The label with the dish and date", "पकवान और तारीख़ वाला लेबल"],
          ["Nothing", "कुछ नहीं"],
          ["Your phone number, by hand", "हाथ से लिखा आपका फ़ोन नंबर"],
        ],
        0,
      ],
      [
        ["A tiffin lid is cracked. What do you do?", "टिफ़िन का ढक्कन टूटा है। आप क्या करेंगी?"],
        [
          ["Use it with tape", "टेप लगाकर इस्तेमाल करें"],
          ["Use it if the food fits", "खाना आ जाए तो इस्तेमाल करें"],
          ["Take a new tiffin and set the cracked one aside", "नया टिफ़िन लें, टूटा अलग रखें"],
        ],
        2,
      ],
      [
        ["How full should a box be?", "डिब्बा कितना भरें?"],
        [
          ["Overflowing, to be generous", "छलकने तक, दिल खोलकर"],
          ["To the line, with a gap under the lid", "लाइन तक, ढक्कन के नीचे जगह छोड़कर"],
          ["Half empty", "आधा खाली"],
        ],
        1,
      ],
      [
        ["Where do hot and cold dishes go?", "गरम और ठंडे पकवान कहाँ रखें?"],
        [
          ["Together in one box", "एक ही डिब्बे में"],
          ["In separate boxes", "अलग-अलग डिब्बों में"],
          ["Cold on top of hot", "ठंडा गरम के ऊपर"],
        ],
        1,
      ],
    ],
    version: 1,
  },
  {
    code: "captain_handover",
    title: ["Handing over at the door", "दरवाज़े पर सौंपना"],
    summary: [
      "Bring each bag to the right flat and record what happens.",
      "हर बैग सही फ़्लैट तक पहुँचाएँ और जो हुआ दर्ज करें।",
    ],
    minutes: 8,
    for_kinds: ["captain"],
    questions: [
      [
        ["When do you hand a box over?", "बॉक्स कब सौंपें?"],
        [
          [
            "On your society’s delivery day, in the slot shown in the app",
            "आपकी सोसाइटी के डिलीवरी दिन, ऐप में दिए समय में",
          ],
          ["Whenever you are free", "जब भी समय मिले"],
          ["Only on Sundays", "सिर्फ़ रविवार को"],
        ],
        0,
      ],
      [
        [
          "A flat is locked and nobody answers. What do you do?",
          "फ़्लैट बंद है और कोई जवाब नहीं देता। क्या करें?",
        ],
        [
          ["Leave the bag outside the door", "बैग दरवाज़े के बाहर छोड़ दें"],
          ["Mark it in the app so the hub can reschedule", "ऐप में दर्ज करें ताकि हब दोबारा तय करे"],
          ["Give it to a neighbour", "पड़ोसी को दे दें"],
        ],
        1,
      ],
      [
        ["A bag arrives with a torn seal. What do you do?", "बैग की सील फटी हुई आए तो क्या करें?"],
        [
          ["Hand it over as usual", "हमेशा की तरह दे दें"],
          ["Take a photo and report it in the app", "फ़ोटो लें और ऐप में बताएँ"],
          ["Throw it away", "फेंक दें"],
        ],
        1,
      ],
      [
        [
          "A family wants a different item in the bag. What do you do?",
          "कोई परिवार बैग में दूसरी चीज़ माँगे तो क्या करें?",
        ],
        [
          ["Swap it from another bag", "दूसरे बैग से बदल दें"],
          [
            "Tell them to ask in the app; you only hand over",
            "उन्हें ऐप में माँगने को कहें; आप सिर्फ़ सौंपती हैं",
          ],
          ["Open the bag and choose", "बैग खोलकर चुन लें"],
        ],
        1,
      ],
      [
        ["Who can you give a bag to?", "बैग किसे दे सकती हैं?"],
        [
          [
            "The family at the flat, or whoever they named in the app",
            "फ़्लैट के परिवार को, या ऐप में बताए व्यक्ति को",
          ],
          ["Anyone at the gate", "गेट पर किसी को भी"],
          ["The security guard, always", "हमेशा सिक्योरिटी गार्ड को"],
        ],
        0,
      ],
    ],
    version: 1,
  },
  {
    code: "rider_safety",
    title: ["Safe driving and crates", "सुरक्षित ड्राइविंग और क्रेट"],
    summary: [
      "Check the van, drive carefully, load the crates properly.",
      "वैन जाँचें, सावधानी से चलाएँ और क्रेट ठीक से रखें।",
    ],
    minutes: 8,
    for_kinds: ["rider"],
    questions: [
      [
        ["Before you start the van, what do you check?", "वैन चलाने से पहले क्या जाँचें?"],
        [
          ["Brakes, tyres and that the load is secured", "ब्रेक, टायर और सामान बँधा है"],
          ["Only the fuel", "सिर्फ़ ईंधन"],
          ["Nothing, the hub checks", "कुछ नहीं, हब जाँचता है"],
        ],
        0,
      ],
      [
        ["Your phone rings while you are driving.", "गाड़ी चलाते समय फ़ोन बजता है।"],
        [
          ["Pick up and talk", "उठाकर बात करें"],
          ["Pull over safely, then call back", "सुरक्षित जगह रुककर फिर फ़ोन करें"],
          ["Text while driving", "चलाते हुए मैसेज करें"],
        ],
        1,
      ],
      [
        ["It is raining hard and the road is flooded.", "तेज़ बारिश है और सड़क पर पानी भरा है।"],
        [
          ["Drive through quickly", "तेज़ी से निकल जाएँ"],
          [
            "Stop, tell the hub and wait for a safe route",
            "रुकें, हब को बताएँ और सुरक्षित रास्ते का इंतज़ार करें",
          ],
          ["Take the footpath", "फुटपाथ से जाएँ"],
        ],
        1,
      ],
      [
        ["How do you carry the crates in the van?", "वैन में क्रेट कैसे रखें?"],
        [
          ["Stacked and strapped, heavy ones at the bottom", "एक पर एक, बाँधकर, भारी नीचे"],
          ["Loose, anywhere", "खुले, कहीं भी"],
          ["On the front seat", "आगे की सीट पर"],
        ],
        0,
      ],
      [
        [
          "You have an accident and nobody is hurt. What do you do?",
          "दुर्घटना हो गई, किसी को चोट नहीं आई। क्या करें?",
        ],
        [
          ["Drive away", "चले जाएँ"],
          ["Stop, take photos and call the hub", "रुकें, फ़ोटो लें और हब को फ़ोन करें"],
          ["Argue with the other driver", "दूसरे ड्राइवर से बहस करें"],
        ],
        1,
      ],
    ],
    version: 1,
  },
];

// ---- Progress, kept per person like server rows (M8 §3.2): best score and a pass kept for good.
const KEY = "gb-mock-training";
/** The backend seed's field people (README): verified, with every required module passed. */
export const SEED_PARTNERS = /** @type {Record<string, string>} */ ({
  "+919800001004": "captain",
  "+919800001005": "kitchen",
  "+919800001006": "rider",
  "+919800001007": "packer",
});
/** @typedef {{ best_score: number, passed: boolean, passed_at: string | null, tries: Record<string, number> }} Progress */
/** @returns {Record<string, Record<string, Progress>>} progress by phone, then module */
const load = () => JSON.parse(localStorage.getItem(KEY) ?? "{}");

/** What this person has done: the seed's passes under what they did in the app. @param {string} phone @returns {Record<string, Progress>} */
export function progressOf(phone) {
  /** @type {Record<string, Progress>} */
  const seed = {};
  const kind = SEED_PARTNERS[phone];
  for (const code of kind ? REQUIRED[kind] : [])
    seed[code] = {
      best_score: kind === "kitchen" && code === "hygiene" ? 0.8 : 1,
      passed: true,
      passed_at: "2026-09-12T10:00:00Z",
      tries: {},
    };
  return { ...seed, ...load()[phone] };
}

/**
 * Keep an attempt: the best score stays, a pass is never undone. @param {string} phone @param {string} code
 * @param {number} score @param {boolean} passed @param {string} day IST date of the attempt
 */
export function recordAttempt(phone, code, score, passed, day) {
  const all = load();
  const was = progressOf(phone)[code];
  all[phone] = {
    ...all[phone],
    [code]: {
      best_score: Math.max(score, was?.best_score ?? 0),
      passed: passed || !!was?.passed,
      passed_at: was?.passed_at ?? (passed ? new Date().toISOString() : null),
      tries: { ...was?.tries, [day]: (was?.tries[day] ?? 0) + 1 },
    },
  };
  localStorage.setItem(KEY, JSON.stringify(all));
}
