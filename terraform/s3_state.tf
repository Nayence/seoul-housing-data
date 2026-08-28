# Bucket dedie au stockage de l'etat Terraform.
#
# POURQUOI UN BUCKET SEPARE de la couche brute :
#   - l'etat contient des valeurs potentiellement sensibles
#   - il a un cycle de vie different (versionnement critique, pas d'archivage)
#   - son acces se restreint plus tard a la chaine de deploiement uniquement
#
# NOTE D'ARCHITECTURE : ce bucket est gere par la configuration qui stocke
# son propre etat dedans. C'est un cercle assume et courant sur un projet
# solo, neutralise par prevent_destroy. En equipe, on l'isolerait dans une
# configuration "bootstrap" separee, appliquee une fois et jamais retouchee.

resource "aws_s3_bucket" "tfstate" {
  bucket = var.state_bucket_name

  # Sans ce garde-fou, un destroy supprimerait le bucket contenant l'etat
  # qui decrit le destroy en cours. Terraform perdrait la memoire de tout
  # ce qu'il gere, et il faudrait tout reimporter a la main.
  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_public_access_block" "tfstate" {
  bucket = aws_s3_bucket.tfstate.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "tfstate" {
  bucket = aws_s3_bucket.tfstate.id

  # NON NEGOCIABLE ici. Un etat corrompu ou ecrase se restaure depuis une
  # version anterieure. Sans versionnement, un incident sur ce fichier
  # signifie reconstruire l'inventaire de l'infrastructure a la main.
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "tfstate" {
  bucket = aws_s3_bucket.tfstate.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "tfstate" {
  bucket = aws_s3_bucket.tfstate.id

  # Pas d'archivage en stockage froid : l'etat doit rester immediatement
  # lisible. On se contente de purger les tres anciennes versions, qui
  # s'accumulent a chaque apply.
  rule {
    id     = "purger-anciennes-versions"
    status = "Enabled"

    filter {}

    noncurrent_version_expiration {
      noncurrent_days = 90
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }

  depends_on = [aws_s3_bucket_versioning.tfstate]
}
