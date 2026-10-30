/**
 * A one-page PDF with one line of text, as a link a tab can open. The mock of GET /v1/earnings/certificate
 * uses it so the download works without a server (the real API returns a short-lived signed link).
 * @param {string} text plain text without parentheses
 */
export function mockPdfUrl(text) {
  const stream = `BT /F1 12 Tf 48 780 Td (${text}) Tj ET`;
  const objects = [
    "<< /Type /Catalog /Pages 2 0 R >>",
    "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
    "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
    `<< /Length ${stream.length} >>\nstream\n${stream}\nendstream`,
    "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
  ];
  let pdf = "%PDF-1.4\n";
  const offsets = objects.map((body, i) => {
    const at = pdf.length;
    pdf += `${i + 1} 0 obj\n${body}\nendobj\n`;
    return at;
  });
  const xref = pdf.length;
  pdf += `xref\n0 ${objects.length + 1}\n0000000000 65535 f \n`;
  pdf += offsets.map((o) => `${String(o).padStart(10, "0")} 00000 n \n`).join("");
  pdf += `trailer\n<< /Size ${objects.length + 1} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF`;
  // A browser will not open a data: URL in a tab (the Download button does that), a blob: URL it will.
  if (typeof URL.createObjectURL === "function")
    return URL.createObjectURL(new Blob([pdf], { type: "application/pdf" }));
  return `data:application/pdf;base64,${btoa(pdf)}`;
}
