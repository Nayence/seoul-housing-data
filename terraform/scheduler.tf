# Declencheur mensuel.
#
# EventBridge Scheduler plutot que les anciennes regles EventBridge : il
# gere nativement les fuseaux horaires, ce qui evite de raisonner en UTC
# pour une donnee publiee selon le calendrier coreen.

resource "aws_scheduler_schedule" "monthly_collect" {
  name        = "${var.project_name}-monthly-collect"
  description = "Reingere les derniers mois de transactions locatives"

  flexible_time_window {
    # AWS peut decaler l'execution dans une fenetre de 15 minutes, ce qui
    # lisse la charge de son cote. Sans importance pour un traitement par
    # lot mensuel, et cela evite les pics a la seconde pile.
    mode                      = "FLEXIBLE"
    maximum_window_in_minutes = 15
  }

  # Le 5 de chaque mois a 3h du matin, heure de Seoul. Le 5 plutot que le
  # 1er : cela laisse quelques jours pour que les declarations du mois
  # precedent commencent a remonter.
  schedule_expression          = "cron(0 3 5 * ? *)"
  schedule_expression_timezone = "Asia/Seoul"

  target {
    arn      = aws_lambda_function.scheduler.arn
    role_arn = aws_iam_role.eventbridge.arn

    # Charge utile vide : le planificateur applique alors son mode
    # automatique, c'est-a-dire les LOOKBACK_MONTHS derniers mois sur les
    # 25 arrondissements.
    input = jsonencode({})

    retry_policy {
      maximum_retry_attempts       = 3
      maximum_event_age_in_seconds = 3600
    }
  }
}
