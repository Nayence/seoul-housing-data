# Lambda de transformation : couche brute -> agregats publies.

resource "aws_cloudwatch_log_group" "transformer" {
  name              = "/aws/lambda/${var.project_name}-transformer"
  retention_in_days = var.log_retention_days
}

resource "aws_iam_role" "transformer" {
  name               = "${var.project_name}-transformer"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

data "aws_iam_policy_document" "transformer" {
  statement {
    sid       = "Logs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.transformer.arn}:*"]
  }

  # Lecture seule de la couche brute. La transformation ne doit jamais
  # pouvoir modifier ou supprimer la source de verite.
  statement {
    sid       = "ReadRawData"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.raw.arn}/normalized/*"]
  }

  # ListBucket porte sur le bucket lui-meme, pas sur les objets — c'est
  # une confusion tres frequente. La condition restreint l'enumeration au
  # seul prefixe utile.
  statement {
    sid       = "ListRawData"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.raw.arn]

    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["normalized/*"]
    }
  }

  # Ecriture des agregats, limitee au prefixe data/.
  statement {
    sid       = "WriteAggregates"
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.site.arn}/data/*"]
  }
}

resource "aws_iam_role_policy" "transformer" {
  name   = "${var.project_name}-transformer"
  role   = aws_iam_role.transformer.id
  policy = data.aws_iam_policy_document.transformer.json
}

resource "aws_lambda_function" "transformer" {
  function_name = "${var.project_name}-transformer"
  role          = aws_iam_role.transformer.arn
  handler       = "handler_transformer.handler"
  runtime       = "python3.13"
  architectures = ["arm64"]

  filename         = data.archive_file.lambda.output_path
  source_code_hash = data.archive_file.lambda.output_base64sha256

  # 1,27 million d'enregistrements a agreger, deux passes sur ~800 Mo
  # telecharges depuis S3. Le maximum de 15 minutes laisse de la marge.
  timeout = 900

  # Sur Lambda, la puissance CPU est proportionnelle a la memoire allouee.
  # Monter la memoire accelere donc le traitement ET le telechargement
  # parallele, ce qui peut reduire le cout total malgre un tarif horaire
  # plus eleve. A mesurer sur les premieres executions reelles.
  memory_size = 2048

  environment {
    variables = {
      RAW_BUCKET  = aws_s3_bucket.raw.id
      SITE_BUCKET = aws_s3_bucket.site.id
    }
  }

  depends_on = [
    aws_iam_role_policy.transformer,
    aws_cloudwatch_log_group.transformer,
  ]
}


# --- Declencheur ----------------------------------------------------------
# POURQUOI UN HORAIRE FIXE plutot qu'un declenchement a la fin de la
# collecte : il n'existe pas de signal fiable "la file est vide" sans
# machinerie supplementaire. Un decalage de deux heures apres le
# planificateur est simple, lisible, et largement suffisant — la collecte
# complete prend une dizaine de minutes.
#
# En cas de besoin, la transformation reste invocable a la main.

resource "aws_iam_role_policy" "eventbridge_transformer" {
  name = "${var.project_name}-eventbridge-transformer"
  role = aws_iam_role.eventbridge.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid      = "InvokeTransformer"
      Effect   = "Allow"
      Action   = "lambda:InvokeFunction"
      Resource = aws_lambda_function.transformer.arn
    }]
  })
}

resource "aws_scheduler_schedule" "monthly_transform" {
  name        = "${var.project_name}-monthly-transform"
  description = "Recalcule les agregats apres la collecte mensuelle"

  flexible_time_window {
    mode                      = "FLEXIBLE"
    maximum_window_in_minutes = 15
  }

  # Le 5 a 5h, soit deux heures apres le planificateur de collecte.
  schedule_expression          = "cron(0 5 5 * ? *)"
  schedule_expression_timezone = "Asia/Seoul"

  target {
    arn      = aws_lambda_function.transformer.arn
    role_arn = aws_iam_role.eventbridge.arn
    input    = jsonencode({})

    retry_policy {
      maximum_retry_attempts       = 2
      maximum_event_age_in_seconds = 3600
    }
  }
}
