// M8 training modules (US-3): short lessons, each ending in a quiz; the score is kept per module.
// The content would come from the hub's CMS in English, Hindi and Marathi (M8 open question); the
// mock has English and Hindi. Each question: [text, options, index of the safe answer, why].
// Answers are not secret: this is practice, and the server still scores what is sent.

/** @typedef {[string, string]} EnHi */
/** @typedef {[EnHi, EnHi[], number, EnHi]} Question */

/** @type {{ code: string, title: EnHi, questions: Question[] }[]} */
export const MODULES = [
  {
    code: "hygiene",
    title: ["Clean hands, clean kitchen", "साफ़ हाथ, साफ़ रसोई"],
    questions: [
      [
        ["Before you start cooking, what comes first?", "खाना बनाना शुरू करने से पहले सबसे पहले क्या?"],
        [
          ["Wash hands with soap for 20 seconds", "साबुन से 20 सेकंड हाथ धोएँ"],
          ["Wipe hands on your dupatta", "दुपट्टे से हाथ पोंछ लें"],
          ["Rinse hands with water only", "सिर्फ़ पानी से हाथ धो लें"],
        ],
        0,
        [
          "Soap and 20 seconds remove germs that water alone leaves behind.",
          "साबुन और 20 सेकंड से वे कीटाणु भी हटते हैं जो सिर्फ़ पानी से नहीं हटते।",
        ],
      ],
      [
        ["You have a cut on your finger. What do you do?", "उँगली पर कट लगा है। आप क्या करेंगी?"],
        [
          ["Cook as usual", "हमेशा की तरह खाना बनाएँ"],
          ["Cover it with a waterproof bandage and wear a glove", "वॉटरप्रूफ़ पट्टी लगाकर दस्ताना पहनें"],
          ["Wash it and leave it open", "धोकर खुला छोड़ दें"],
        ],
        1,
        ["A covered cut keeps germs out of the food.", "ढका हुआ कट खाने में कीटाणु नहीं जाने देता।"],
      ],
      [
        ["When do you clean the counter?", "रसोई का प्लेटफ़ॉर्म कब साफ़ करें?"],
        [
          ["Once a week", "हफ़्ते में एक बार"],
          ["Before and after cooking", "खाना बनाने से पहले और बाद में"],
          ["Only when it looks dirty", "सिर्फ़ गंदा दिखने पर"],
        ],
        1,
        [
          "Germs don’t show. Clean before and after every cooking session.",
          "कीटाणु दिखते नहीं; हर बार खाना बनाने से पहले और बाद में साफ़ करें।",
        ],
      ],
    ],
  },
  {
    code: "storage",
    title: ["Safe storage", "सुरक्षित रखना"],
    questions: [
      [
        ["Where do you keep paneer and curd?", "पनीर और दही कहाँ रखें?"],
        [
          ["In the fridge, covered", "फ़्रिज में, ढककर"],
          ["On the kitchen shelf", "रसोई की शेल्फ़ पर"],
          ["Near the stove to keep warm", "गरम रखने के लिए चूल्हे के पास"],
        ],
        0,
        ["Dairy spoils fast outside the fridge.", "दूध से बनी चीज़ें फ़्रिज के बाहर जल्दी ख़राब होती हैं।"],
      ],
      [
        ["How do you keep atta and dal?", "आटा और दाल कैसे रखें?"],
        [
          ["In the open sack", "खुली बोरी में"],
          ["In airtight containers, off the floor", "हवाबंद डिब्बों में, ज़मीन से ऊपर"],
          ["Under the sink", "सिंक के नीचे"],
        ],
        1,
        [
          "Airtight containers keep out damp, insects and rats.",
          "हवाबंद डिब्बे नमी, कीड़ों और चूहों से बचाते हैं।",
        ],
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
        [
          "Food left warm grows bacteria fast. Leftovers never go into a GharBasket tiffin.",
          "गरम पड़ा खाना जल्दी ख़राब होता है। बचा हुआ खाना GharBasket टिफ़िन में कभी नहीं जाता।",
        ],
      ],
      [
        ["How do raw and cooked food sit in the fridge?", "फ़्रिज में कच्चा और पका खाना कैसे रखें?"],
        [
          ["Cooked above raw, both covered", "पका ऊपर, कच्चा नीचे, दोनों ढके"],
          ["Raw food on top", "कच्चा खाना ऊपर"],
          ["Together in one bowl", "दोनों एक ही बर्तन में"],
        ],
        0,
        [
          "Raw food can drip; keeping it below keeps cooked food safe.",
          "कच्चे खाने से पानी टपक सकता है; उसे नीचे रखने से पका खाना सुरक्षित रहता है।",
        ],
      ],
      [
        ["The milk smells sour. What do you do?", "दूध से खट्टी गंध आ रही है। आप क्या करेंगी?"],
        [
          ["Boil it and use it", "उबालकर इस्तेमाल करें"],
          ["Mix it with fresh milk", "ताज़े दूध में मिला दें"],
          ["Don’t use it for tiffins", "टिफ़िन में इस्तेमाल न करें"],
        ],
        2,
        [
          "When in doubt, leave it out. Everything in a tiffin must be fresh.",
          "शक हो तो इस्तेमाल न करें। टिफ़िन की हर चीज़ ताज़ा होनी चाहिए।",
        ],
      ],
    ],
  },
  {
    code: "packing",
    title: ["Packing tiffins", "टिफ़िन पैक करना"],
    questions: [
      [
        ["When do you close the tiffin?", "टिफ़िन कब बंद करें?"],
        [
          ["While the food is still steaming", "जब खाना भाप छोड़ रहा हो"],
          ["Once the steam stops, within 30 minutes", "भाप रुकने के बाद, 30 मिनट के अंदर"],
          ["The next morning", "अगली सुबह"],
        ],
        1,
        [
          "Closing in steam traps water; waiting longer lets germs grow.",
          "भाप में बंद करने से पानी जमता है; ज़्यादा देर रुकने से कीटाणु बढ़ते हैं।",
        ],
      ],
      [
        ["What goes on every tiffin?", "हर टिफ़िन पर क्या लगेगा?"],
        [
          ["The label with the dish and date", "पकवान और तारीख़ वाला लेबल"],
          ["Nothing", "कुछ नहीं"],
          ["Your phone number, by hand", "हाथ से लिखा आपका फ़ोन नंबर"],
        ],
        0,
        [
          "The label tells the family what is inside and when it was cooked.",
          "लेबल से परिवार को पता चलता है कि अंदर क्या है और कब बना।",
        ],
      ],
      [
        ["A tiffin lid is cracked. What do you do?", "टिफ़िन का ढक्कन टूटा है। आप क्या करेंगी?"],
        [
          ["Use it with tape", "टेप लगाकर इस्तेमाल करें"],
          ["Use it if the food fits", "खाना आ जाए तो इस्तेमाल करें"],
          ["Take a new tiffin and set the cracked one aside", "नया टिफ़िन लें, टूटा अलग रखें"],
        ],
        2,
        ["A cracked lid leaks and lets dust in.", "टूटे ढक्कन से खाना गिरता है और धूल अंदर जाती है।"],
      ],
    ],
  },
  {
    code: "handover",
    title: ["Handing over on time", "समय पर सौंपना"],
    questions: [
      [
        ["When must tiffins be ready for the rider?", "राइडर के लिए टिफ़िन कब तैयार हों?"],
        [
          ["By the pickup time in the app", "ऐप में दिए पिकअप समय तक"],
          ["Whenever they are cooked", "जब भी बन जाएँ"],
          ["After lunch", "दोपहर के खाने के बाद"],
        ],
        0,
        [
          "The rider has a route; one late kitchen delays every home on it.",
          "राइडर का एक रूट होता है; एक रसोई की देरी से रूट के हर घर में देर होती है।",
        ],
      ],
      [
        ["You can’t cook tomorrow. What do you do?", "कल आप खाना नहीं बना पाएँगी। क्या करें?"],
        [
          ["Ask a neighbour to cook", "पड़ोसी से बनवा लें"],
          ["Tell the hub in the app before 9:30 PM", "रात 9:30 से पहले ऐप में हब को बताएँ"],
          ["Don’t answer the door", "दरवाज़ा न खोलें"],
        ],
        1,
        [
          "The hub moves your orders to another kitchen in time.",
          "हब समय रहते आपके ऑर्डर दूसरी रसोई को दे देता है।",
        ],
      ],
      [
        ["How do you hand tiffins to the rider?", "राइडर को टिफ़िन कैसे दें?"],
        [
          ["Counted, in the crate, marked Ready in the app", "गिनकर, क्रेट में, ऐप में 'तैयार' दबाकर"],
          ["Loose in a bag", "थैली में खुले"],
          ["Outside the door", "दरवाज़े के बाहर"],
        ],
        0,
        [
          "Counting and marking Ready means nothing is missed.",
          "गिनने और 'तैयार' दबाने से कुछ नहीं छूटता।",
        ],
      ],
    ],
  },
  {
    code: "money",
    title: ["Earnings and the app", "कमाई और ऐप"],
    questions: [
      [
        ["When are your earnings paid?", "आपकी कमाई कब मिलती है?"],
        [
          ["Every week, to your bank", "हर हफ़्ते, आपके बैंक में"],
          ["In cash from the rider", "राइडर से नकद"],
          ["Once a year", "साल में एक बार"],
        ],
        0,
        [
          "Earnings come from the ledger every week, straight to your checked bank account.",
          "कमाई हर हफ़्ते लेजर से सीधे आपके जाँचे गए बैंक खाते में आती है।",
        ],
      ],
      [
        ["A customer asks for money back in cash. What do you do?", "ग्राहक नकद पैसे वापस माँगे तो?"],
        [
          ["Pay from your pocket", "अपनी जेब से दे दें"],
          ["Tell them refunds come through the app", "बताएँ कि रिफ़ंड ऐप से आता है"],
          ["Ignore them", "अनसुना कर दें"],
        ],
        1,
        [
          "Refunds go through the ledger, so everyone’s money is recorded.",
          "रिफ़ंड लेजर से जाता है ताकि सबका पैसा दर्ज रहे।",
        ],
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
        ["GharBasket never asks for your OTP.", "GharBasket कभी आपका OTP नहीं माँगता।"],
      ],
    ],
  },
];
