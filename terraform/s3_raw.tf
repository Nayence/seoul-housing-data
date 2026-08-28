# Couche brute : les donnees collectees, telles qu'ingerees.
#
# Ce bucket ne doit JAMAIS etre accessible publiquement. Il contient la source
# de verite du pipeline : tout retraitement repart de la.
#
# NOTE : ces ressources existent deja (creees a la main via l'AWS CLI).
# Les blocs "import" en bas de fichier disent a Terraform de les adopter au
# lieu de tenter de les creer.

resource "aws_s3_bucket" "raw" {
  bucket = var.raw_bucket_name

  # Garde-fou : empeche un "terraform destroy" de supprimer le bucket et
  # toutes les donnees collectees. Pour le supprimer volontairement, il faut
  # d'abord retirer cette ligne — ce qui force a y reflechir.
  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_public_access_block" "raw" {
  bucket = aws_s3_bucket.raw.id

  # AWS applique ces reglages par defaut depuis quelques annees, mais les
  # declarer rend l'intention explicite et empeche une desactivation
  # accidentelle depuis la console.
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "raw" {
  bucket = aws_s3_bucket.raw.id

  # Le filet de securite du pipeline : chaque ecrasement conserve la version
  # precedente. Indispensable le jour ou un bug de collecte ecrase des mois
  # de donnees.
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "raw" {
  bucket = aws_s3_bucket.raw.id

  # Chiffrement au repos avec cles gerees par S3. Actif par defaut depuis
  # 2023, gratuit, mais declare explicitement : un auditeur veut le voir
  # dans le code, pas le deviner.
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "raw" {
  bucket = aws_s3_bucket.raw.id

  # Sans cette regle, le versionnement fait grossir le stockage indefiniment.
  # Les anciennes versions basculent en Glacier Instant Retrieval au bout d'un
  # mois, puis disparaissent au bout d'un an.
  rule {
    id     = "archiver-anciennes-versions"
    status = "Enabled"

    filter {}

    noncurrent_version_transition {
      noncurrent_days = var.noncurrent_transition_days
      storage_class   = "GLACIER_IR"
    }

    noncurrent_version_expiration {
      noncurrent_days = var.noncurrent_expiration_days
    }

    # Nettoie les fragments d'envois multipart interrompus, qui sont
    # factures alors qu'ils ne servent a rien. Oubli tres frequent.
    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }

  depends_on = [aws_s3_bucket_versioning.raw]
}


# --- Import de l'existant ------------------------------------------------
#
# Les blocs "import" (Terraform 1.5+) sont declaratifs : ils apparaissent
# dans le plan, on voit ce qui va etre adopte avant de valider. C'est plus
# sur que l'ancienne commande "terraform import", qui agissait aveuglement.
#
# Une fois l'import realise avec succes, ces blocs peuvent etre supprimes :
# ils ne servent qu'une fois.

import {
  to = aws_s3_bucket.raw
  id = "seoul-housing-raw-anice"
}

import {
  to = aws_s3_bucket_public_access_block.raw
  id = "seoul-housing-raw-anice"
}

import {
  to = aws_s3_bucket_versioning.raw
  id = "seoul-housing-raw-anice"
}

import {
  to = aws_s3_bucket_server_side_encryption_configuration.raw
  id = "seoul-housing-raw-anice"
}

import {
  to = aws_s3_bucket_lifecycle_configuration.raw
  id = "seoul-housing-raw-anice"
}
