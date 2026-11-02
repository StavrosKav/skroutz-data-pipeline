"""HTML shell and badge helpers for the pipeline emails (moved from run_pipeline.py)."""
import datetime


def _html_shell(title: str, subtitle: str, body: str) -> str:
    today = datetime.date.today().strftime("%B %d, %Y")
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
</head>
<body style="margin:0;padding:0;background:#f0f2f5;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#f0f2f5;padding:32px 12px;">
    <tr><td align="center">
      <table cellpadding="0" cellspacing="0" style="max-width:620px;width:100%;background:#ffffff;border-radius:10px;overflow:hidden;box-shadow:0 4px 18px rgba(0,0,0,0.11);">
        <tr>
          <td style="background:#0d1b2a;padding:0;">
            <div style="border-left:4px solid #e63946;padding:22px 26px;">
              <p style="margin:0 0 4px;font-size:10px;color:#e63946;letter-spacing:2.5px;text-transform:uppercase;font-weight:700;">Skroutz Price Tracker</p>
              <h1 style="margin:0;font-size:19px;color:#ffffff;font-weight:700;line-height:1.3;">{title}</h1>
              <p style="margin:5px 0 0;font-size:12px;color:#7a8fa6;">{subtitle}</p>
            </div>
          </td>
        </tr>
        <tr>
          <td style="padding:26px 26px 18px;">
            {body}
          </td>
        </tr>
        <tr>
          <td style="background:#f8fafc;padding:13px 26px;border-top:1px solid #e8ecf0;">
            <p style="margin:0;font-size:11px;color:#aab0bb;">
              Skroutz Price Tracker &nbsp;·&nbsp; {today} &nbsp;·&nbsp;
              <a href="https://www.skroutz.gr" style="color:#aab0bb;text-decoration:none;">skroutz.gr</a>
            </p>
          </td>
        </tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""


def _pct_badge(pct: float) -> str:
    val = abs(pct)
    if val >= 30:
        bg, fg = "#1a6b35", "#ffffff"
    elif val >= 20:
        bg, fg = "#2d9e4f", "#ffffff"
    elif val >= 10:
        bg, fg = "#52b87c", "#ffffff"
    else:
        bg, fg = "#d4edda", "#1a6b35"
    return (
        f'<span style="background:{bg};color:{fg};padding:3px 8px;border-radius:12px;'
        f'font-size:11px;font-weight:700;white-space:nowrap;">&#x2193; {val:.1f}%</span>'
    )
