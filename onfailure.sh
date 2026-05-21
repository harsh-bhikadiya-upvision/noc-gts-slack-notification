#!/bin/bash
echo "🚨 Build status is verified as FAILURE. Dispatched notification payload..."
PAYLOAD=$(
  cat <<EOF
    {
  "blocks": [
    {
      "type": "section",
      "text": {
        "type": "mrkdwn",
        "text": "🚨 *NOC GTS Alert:* Slack notification script failure in *${JOB_NAME}* (Build #${BUILD_NUMBER})."
      }
    },
    {
      "type": "actions",
      "elements": [
        {
          "type": "button",
          "text": {
            "type": "plain_text",
            "text": "🔍 View Logs",
            "emoji": true
          },
          "url": "${BUILD_URL}console",
          "style": "danger"
        }
      ]
    }
  ]
}
EOF
)

curl -X POST -H 'Content-type: application/json' --data "$PAYLOAD" "$SLACK_WEBHOOK_URL"
